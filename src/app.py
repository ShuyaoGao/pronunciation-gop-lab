"""
AI 语言听说测评工具 — 网页（Gradio）。第二阶段：多维评分 + 听说闭环 + 学习档案 + CEFR 自适应。

四个标签页：
  ① 发音评分：选/改一句 → 听母语示范(TTS) → 录音/上传 → 多维评分(准确/完整/流利/韵律) + 逐音素红绿图 + 纠错；
  ② AI 对话陪练：CEFR 自适应难度的口语对话，回复可朗读(TTS)，附简短纠错；
  ③ 学习档案：学习曲线、高频错音、间隔重复推荐（全部本地存储）；
  ④ 关于/原理：讲清 GOP 原理与本工具能/不能做什么。

运行： <env-python> src/app.py  然后打开 http://127.0.0.1:7860
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import os

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT_DIR))          # 启动外壳放在项目根目录的 lan_share.py / lan_web.py

LOCAL_MODEL = ROOT_DIR / "models" / "wav2vec2-xls-r-300m-timit-phoneme"
HF_CACHE_NAME = "models--vitouphy--wav2vec2-xls-r-300m-timit-phoneme"


def _prepare_model_env():
    """在 import config 之前:项目 models/ 里放了模型就离线用;否则没设镜像又连不上 HuggingFace 时自动用 hf-mirror。"""
    if not os.environ.get("AISPEAK_MODEL") and (LOCAL_MODEL / "config.json").exists():
        os.environ["AISPEAK_MODEL"] = str(LOCAL_MODEL)
        print(f"[提示] 已检测到本地模型 {LOCAL_MODEL.relative_to(ROOT_DIR)},离线使用,不再下载。")
        return
    if os.environ.get("AISPEAK_MODEL") or os.environ.get("HF_ENDPOINT"):
        return
    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub" / HF_CACHE_NAME
    if hub.exists():
        return
    import urllib.request
    try:
        urllib.request.urlopen("https://huggingface.co/api/models/vitouphy/wav2vec2-xls-r-300m-timit-phoneme",
                               timeout=4).close()
    except Exception:
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
        print("[提示] 连不上 huggingface.co,发音模型改从国内镜像 hf-mirror.com 下载(首次评分时,约 1.2GB)。")


_prepare_model_env()

import gradio as gr
import pandas as pd

import config
import azure_compare
import cefr
import history as hist
import tts
from chat_coach import adaptive_reply
from feedback import attach_hints, result_to_html
from pronunciation import get_scorer

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

with open(config.SENTENCES_PATH, "r", encoding="utf-8") as f:
    SENTENCES = json.load(f)
LEVELS = [k for k in SENTENCES.keys() if not k.startswith("_")]


# ========== 发音评分 ==========
def on_level_change(level):
    choices = SENTENCES.get(level, [])
    first = choices[0] if choices else ""
    return gr.update(choices=choices, value=first), first


def on_sentence_pick(sentence):
    return sentence


def play_reference(reference_text):
    """朗读目标参考句（听母语示范，先听再模仿）。"""
    if not reference_text.strip():
        return None
    return tts.synthesize(reference_text)


def do_score(reference_text, audio_path, use_azure, level):
    if not audio_path:
        return "<div style='color:#cf222e'>请先用麦克风录音，或上传一段音频。</div>", ""
    try:
        scorer = get_scorer()
    except Exception as e:
        return (f"<div style='color:#cf222e'>发音模型加载失败：{e}<br>"
                "首次运行需联网下载模型（约 1.2GB）。详见 README。</div>", "")
    try:
        result = scorer.score(audio_path, reference_text)
    except Exception as e:
        import traceback
        return f"<div style='color:#cf222e'>评分出错：{e}<pre>{traceback.format_exc()}</pre></div>", ""
    attach_hints(result)
    main_html = result_to_html(result)

    # 自动写入学习档案（本地）
    if result.ok:
        try:
            hist.record_session(result, cefr=level)
        except Exception:
            pass

    azure_html = ""
    if use_azure:
        azure_html = _azure_html(azure_compare.assess(audio_path, reference_text))
    return main_html, azure_html


def _azure_html(r) -> str:
    if not r.ok:
        return f"<div style='color:#888'>Azure 对照：{r.message}</div>"
    rows = (f"准确度 {r.accuracy}｜流利度 {r.fluency}｜完整度 {r.completeness}｜综合 {r.pron}")
    phones = ""
    if r.phones:
        chips = "".join(
            f"<span style='margin:0 2px;padding:1px 6px;border-radius:6px;"
            f"background:#eef;border:1px solid #99c'>{p.phone}:{p.score:.0f}</span>"
            for p in r.phones[:60])
        phones = f"<div style='margin-top:6px'>{chips}</div>"
    return (f"<div style='border:1px solid #99c;border-radius:8px;padding:10px;margin-top:8px'>"
            f"<b>Azure 发音评测（商用对照）</b><br>{rows}{phones}</div>")


# ========== 对话陪练 ==========
def do_chat(message, history_msgs, cefr_level, auto_adapt, speak):
    history_msgs = history_msgs or []
    message = (message or "").strip()
    if not message:
        return history_msgs, "", "", None
    reply, info = adaptive_reply(history_msgs, message, target_cefr=cefr_level, auto_adapt=auto_adapt)
    new_history = history_msgs + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": reply},
    ]
    audio = None
    if speak:
        # 只朗读回复正文（去掉 📝 纠错行与离线说明），更自然
        spoken = reply.split("📝")[0].split("（")[0].strip()
        audio = tts.synthesize(spoken) if spoken else None
    info_html = f"<div style='color:#0969da;font-size:13px'>{info}</div>" if info else ""
    return new_history, "", info_html, audio


# ========== 学习档案 ==========
def refresh_history():
    sessions = hist.load_sessions()
    # 学习曲线
    trend = hist.score_trend(sessions)
    trend_df = pd.DataFrame(trend, columns=["练习", "综合分"]) if trend \
        else pd.DataFrame({"练习": [], "综合分": []})
    # 高频错音
    freq = hist.error_phone_frequency(sessions)[:10]
    err_df = pd.DataFrame([{"音素": f"/{i}/", "出错次数": c} for i, c, _ in freq]) if freq \
        else pd.DataFrame({"音素": [], "出错次数": []})
    # 推荐
    rec = hist.recommend(sessions, top_n=3)
    rec_html = _recommend_html(rec, len(sessions))
    return trend_df, err_df, rec_html


def _recommend_html(rec, n_sessions) -> str:
    parts = [f"<div style='font-size:13px;color:#666'>已累计 {n_sessions} 次练习记录。</div>"]
    if rec["phones"]:
        parts.append("<b>最该加强的音（弱项优先 + 间隔重复）：</b><ul>")
        for p in rec["phones"]:
            parts.append(f"<li>/{p['ipa']}/ —— 出错 {p['count']} 次，平均 {p['avg_score']:.0f} 分</li>")
        parts.append("</ul>")
    if rec["sentences"]:
        parts.append("<b>推荐练习句（每句包含一个目标音）：</b><ul>")
        for ipa, sent in rec["sentences"]:
            parts.append(f"<li>练 /{ipa}/：<code>{sent}</code></li>")
        parts.append("</ul>")
    parts.append(f"<div style='color:#888'>{rec['note']}</div>")
    return "".join(parts)


def clear_history_action():
    hist.clear_history()
    empty_trend = pd.DataFrame({"练习": [], "综合分": []})
    empty_err = pd.DataFrame({"音素": [], "出错次数": []})
    return empty_trend, empty_err, "<div>已清空学习档案。</div>"


# ========== 界面 ==========
PRINCIPLE_MD = """
### 核心原理（一句话）
**发音评分 = 正用 ASR 的声学模型 + 强制对齐 + GOP**：ASR 声学模型本就输出「每帧音频属于各音素的概率」。
已知你*应该*读的句子，用强制对齐把录音对齐到这串音素，再看每个音素后验概率高不高——这就是
**GOP（Goodness of Pronunciation）**。概率高=标准（绿）；低/被听成别的音=问题（红）。**正用，不是逆用。**
"""

ABOUT_MD = """
## 关于本工具

**它能做什么**
- 逐音素发音诊断：哪个音不准、被听成了什么、怎么改（本地自研 GOP）。
- 多维评分：音素准确度 + 完整度（漏/多读）+ 流利度（语速/停顿）+ 韵律（语调/重音）。
- 听说闭环：朗读目标句给你模仿；AI 对话陪练能语音回复。
- CEFR 自适应：按你的水平自动调难度（发音差就别硬塞难句）。
- 学习档案：学习曲线 + 高频错音 + 间隔重复推荐，全部本地存储。

**它的局限（诚实）**
- GOP / 韵律 / CEFR 的阈值都是**未标定的经验值**，绝对分仅供课堂演示；分数排序比绝对值更可信。
- CEFR 估计是轻量启发式，不是训练好的分级器；prompt 控难度本身不完全可靠。
- 目前**只做英文**；中文（平翘舌/声调）、韩文留待第二阶段（讯飞 ISE / SenseVoice）。
- 默认全本地、零外部 key；TTS 用 Windows 自带 SAPI（可换更自然的 Kokoro/ElevenLabs）。
- 发音模型：`vitouphy/wav2vec2-xls-r-300m-timit-phoneme`（CPU 即可，单句约 0.3s）。

详见 [README](README.md)。
"""


def build_app(banner_html: str = ""):
    with gr.Blocks(title="AI 语言听说测评工具") as demo:
        if banner_html:
            gr.HTML(banner_html)
        gr.Markdown("# 🗣️ AI 语言听说测评工具\n"
                    "本地自研 **多维发音评分** + **听说闭环** + **CEFR 自适应对话** + **学习档案**。英文优先。")
        gr.Markdown(PRINCIPLE_MD)

        with gr.Tabs():
            # ① 发音评分
            with gr.Tab("① 发音评分"):
                gr.Markdown("选一句（或自己改写）→ 可先「听母语示范」→ 朗读录音 → 点「开始评分」。")
                with gr.Row():
                    level_dd = gr.Dropdown(LEVELS, value=LEVELS[0] if LEVELS else None, label="难度 (CEFR)")
                    sent_dd = gr.Dropdown(SENTENCES.get(LEVELS[0], []) if LEVELS else [],
                                          value=(SENTENCES.get(LEVELS[0], [""])[0] if LEVELS else ""),
                                          label="参考句")
                ref_tb = gr.Textbox(value=(SENTENCES.get(LEVELS[0], [""])[0] if LEVELS else ""),
                                    label="要朗读的句子（可编辑）", lines=2)
                with gr.Row():
                    listen_btn = gr.Button(f"🔊 听母语示范（{tts.backend_name()}）")
                    ref_audio = gr.Audio(label="目标发音示范", type="filepath", autoplay=True)
                audio_in = gr.Audio(sources=["microphone", "upload"], type="filepath",
                                    label="录音 / 上传音频（安静环境、读完整句）")
                with gr.Row():
                    azure_ck = gr.Checkbox(
                        value=False,
                        label=("并排对照 Azure 发音评测" +
                               ("" if config.azure_enabled() else "（未配置 key，将自动跳过）")))
                    score_btn = gr.Button("开始评分", variant="primary")
                if not config.azure_enabled():
                    gr.Markdown("<small>⚠ 勾选 Azure 会把录音发往微软云端；本机未配置 key，默认仅本地处理。评分结果会自动存入「学习档案」。</small>")
                result_html = gr.HTML()
                azure_out = gr.HTML()

                level_dd.change(on_level_change, inputs=level_dd, outputs=[sent_dd, ref_tb])
                sent_dd.change(on_sentence_pick, inputs=sent_dd, outputs=ref_tb)
                listen_btn.click(play_reference, inputs=ref_tb, outputs=ref_audio)
                score_btn.click(do_score, inputs=[ref_tb, audio_in, azure_ck, level_dd],
                                outputs=[result_html, azure_out])

            # ② 对话陪练
            with gr.Tab("② AI 对话陪练"):
                status = ("✅ 已配置 LLM，使用真实模型对话。" if config.llm_enabled()
                          else "ℹ️ 未配置 LLM key，使用【离线规则陪练】演示分级+纠错闭环（配置 OPENAI_API_KEY 即用真实 LLM）。")
                gr.Markdown(f"按 CEFR 等级控难度，母语者口语风格，回答后附简短纠错。\n\n{status}")
                with gr.Row():
                    chat_cefr = gr.Dropdown(LEVELS, value="A2" if "A2" in LEVELS else (LEVELS[0] if LEVELS else None),
                                            label="对话难度 (CEFR)")
                    auto_ck = gr.Checkbox(value=True, label="自动适应难度（按发音历史+你的表达复杂度）")
                    speak_ck = gr.Checkbox(value=True, label=f"朗读 AI 回复（{tts.backend_name()}）")
                chatbot = gr.Chatbot(height=360, label="对话")
                chat_info = gr.HTML()
                chat_audio = gr.Audio(label="AI 语音", type="filepath", autoplay=True)
                with gr.Row():
                    msg_tb = gr.Textbox(placeholder="用英文说点什么…（回车发送）", scale=5, label="你的话")
                    send_btn = gr.Button("发送", variant="primary", scale=1)
                clear_btn = gr.Button("清空对话")

                chat_inputs = [msg_tb, chatbot, chat_cefr, auto_ck, speak_ck]
                chat_outputs = [chatbot, msg_tb, chat_info, chat_audio]
                send_btn.click(do_chat, inputs=chat_inputs, outputs=chat_outputs)
                msg_tb.submit(do_chat, inputs=chat_inputs, outputs=chat_outputs)
                clear_btn.click(lambda: ([], "", "", None), outputs=chat_outputs)

            # ③ 学习档案
            with gr.Tab("③ 学习档案"):
                gr.Markdown("每次发音评分都会自动记录到本地。下面是你的学习曲线、高频错音和练习推荐。")
                with gr.Row():
                    refresh_btn = gr.Button("🔄 刷新", variant="primary")
                    clear_hist_btn = gr.Button("清空档案")
                with gr.Row():
                    trend_plot = gr.LinePlot(x="练习", y="综合分", title="学习曲线（综合分）", height=260)
                    err_plot = gr.BarPlot(x="音素", y="出错次数", title="高频错音 Top10", height=260)
                rec_html = gr.HTML()
                refresh_btn.click(refresh_history, outputs=[trend_plot, err_plot, rec_html])
                clear_hist_btn.click(clear_history_action, outputs=[trend_plot, err_plot, rec_html])
                demo.load(refresh_history, outputs=[trend_plot, err_plot, rec_html])

            # ④ 关于
            with gr.Tab("④ 关于 / 原理"):
                gr.Markdown(ABOUT_MD)

        gr.Markdown("---\n<small>教学演示项目。本地自研 GOP（阈值已在 speechocean762 上标定，跨口音 / 设备仍可能偏移）；多维分含完整/流利/韵律；"
                    "CEFR 为轻量启发式。中文/韩文与端到端实时语音见 README 后续规划。</small>")
    return demo


def main():
    import argparse
    import traceback
    import webbrowser

    ap = argparse.ArgumentParser(description="AI 语言听说测评工具")
    ap.add_argument("--port", type=int, default=7860)
    ap.add_argument("--lan", action="store_true",
                    help="课堂模式:监听 0.0.0.0,显示局域网网址与二维码;默认用 https(手机录音需要)")
    ap.add_argument("--http", action="store_true", help="课堂模式也用 http(手机就不能录音,只能上传音频)")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    try:
        import lan_web
    except ImportError:
        lan_web = None
    scheme, ssl_kw, banner = "http", {}, ""
    if lan_web is None:                       # 没套启动外壳:照旧只在本机跑
        host, port, open_url = ("0.0.0.0" if args.lan else "127.0.0.1"), args.port, f"http://127.0.0.1:{args.port}"
    else:
        if args.lan and not args.http:
            try:
                import lan_share
                pair = lan_web.self_signed_cert(str(ROOT_DIR / ".tools" / "lan-cert"), lan_share.lan_ips())
            except Exception:
                traceback.print_exc()
                pair = None
            if pair:
                scheme = "https"
                ssl_kw = dict(ssl_certfile=pair[0], ssl_keyfile=pair[1], ssl_verify=False)
            else:
                print("[提示] 生成 https 证书失败(没装 cryptography?),课堂模式改用 http:"
                      "学生手机不能录音,只能上传音频文件。")
        ctx = lan_web.setup(args.port, args.lan, title="AI 语言听说测评 · 课堂局域网模式", scheme=scheme)
        host, port, open_url = ctx["host"], ctx["port"], ctx["open_url"]
        if ctx["lan_url"]:
            import lan_share
            note = ("<div style='font-size:14px;margin-top:6px'>浏览器提示<b>「不安全 / 证书无效」</b>时:点"
                    "「高级 / 显示详细信息」→「继续访问」(这是老师电脑的临时证书,只在本教室 WiFi 内有效),"
                    "再允许使用麦克风。请用手机自带浏览器或相机扫码,部分 App 的内置浏览器不能录音。</div>") if scheme == "https" else ""
            banner = lan_share.qr_html(ctx["lan_url"], ctx["urls"][1:]) + note

    app = build_app(banner)
    print(f"\n浏览器会自动打开;如果没有,请手动访问 {open_url}\n关闭本窗口即退出。\n", flush=True)
    app.launch(server_name=host, server_port=port, inbrowser=False, prevent_thread_lock=True,
               show_error=True, theme=gr.themes.Soft(), **ssl_kw)
    if not args.no_browser and not os.environ.get("LAUNCHER_NO_BROWSER"):
        try:
            webbrowser.open(open_url)
        except Exception:
            pass
    try:
        app.block_thread()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
