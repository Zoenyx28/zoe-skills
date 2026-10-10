#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bili-note —— B 站视频链接 → markdown 笔记。

抓取全交给同目录的 bin/yt-dlp.exe（自带 cookie 文件），
这个脚本只负责：分P 选择、去滚动重复、按停顿切段、拼头部、落文件。

子命令：
    run <url> [--pages 3-5] [--print-md]   完整管线
    list <url>                             只看分P 列表 / 单P 信息
    set-output-dir <path>                  记住输出目录
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
YTDLP = os.path.join(SKILL_DIR, "bin", "yt-dlp.exe")
COOKIE_FILE = os.path.join(SKILL_DIR, "cookies.txt")
CONFIG_FILE = os.path.join(SKILL_DIR, "config.json")
# 洗完的稿子放这儿，交给 agent 读去总结。每次 run 清空，只会留当前这一批。
WORK = os.path.join(SKILL_DIR, ".work")

# 切段阈值，可在 config.json 里覆盖
DEFAULT_PAUSE = 1.5        # 停顿超过这么多秒 → 断段
DEFAULT_MAX_CHARS = 150    # 单段累积到这个字数 → 断段

LANG_NAMES = {
    "zh-Hans": "简体中文", "zh-CN": "简体中文", "zh": "简体中文",
    "zh-Hant": "繁體中文", "zh-TW": "繁體中文", "zh-HK": "繁體中文",
    "ai-zh": "简体中文", "en-US": "English", "en-GB": "English", "en": "English",
}


def die(msg, status="error", code=1, **extra):
    print(json.dumps({"status": status, "message": msg, **extra}, ensure_ascii=False))
    sys.exit(code)


# ---------------------------------------------------------------- config

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            return json.load(open(CONFIG_FILE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_config(cfg):
    json.dump(cfg, open(CONFIG_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- yt-dlp

def ytdlp(args, timeout=900):
    cmd = [YTDLP, "--no-warnings", "--ignore-config", *args]
    p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


# ---------------------------------------------------------------- 只读元信息

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def http_json(url, timeout=20):
    """直连。本机 HTTP_PROXY 指向本地代理，代理没开时会拖成超时，而 B 站直连是通的。"""
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def resolve_bvid(url):
    """从链接里抠 BV 号；b23.tv 短链就先跟一次跳转。"""
    m = re.search(r"(BV[0-9A-Za-z]{10})", url)
    if m:
        return m.group(1)
    if "b23.tv" in url or "bilibili.com" not in url:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with op.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20) as r:
                m = re.search(r"(BV[0-9A-Za-z]{10})", r.geturl())
                if m:
                    return m.group(1)
        except Exception as e:
            die(f"打不开这个链接：{e}")
    die(f"链接里找不到 BV 号：{url}")


def probe(url):
    """拿视频/分P 元信息。返回 (是否多P, 分P 列表, 顶层信息)

    只用 /x/web-interface/view 这一个只读端点：无签名、无鉴权、不需要 cookie，
    yt-dlp 自己也走它。字幕抓取那份风险（wbi / 风控 / __INITIAL_STATE__ 改版）
    仍然全在 yt-dlp 身上，这里不存在第二条要维护的解析路径。
    """
    bvid = resolve_bvid(url)
    d = http_json(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
    if d.get("code") != 0:
        die(f"查视频信息失败：code={d.get('code')} {d.get('message')}")
    data = d.get("data") or {}
    pages = [{"index": p.get("page"), "title": p.get("part"), "cid": p.get("cid")}
             for p in (data.get("pages") or [])]
    info = {"title": data.get("title"), "bvid": bvid,
            "uploader": (data.get("owner") or {}).get("name"),
            "count": len(pages)}
    return len(pages) > 1, pages, info


def fetch_subs(url, workdir, items=None, multi=False):
    """把字幕下到 workdir，返回生成的 .srt 绝对路径列表

    单P 和多P 用两套输出模板：多P 才需要 %(playlist_index)03d，而单P 里这个
    字段是 None，硬套会渲染成 NA —— 那样文件名解析会静默把所有 P 归到第 1 集。
    这里绝不依赖"输出模板一定会渲染出数字"，抓完之后还会再核对一次数量。
    """
    tpl = os.path.join(workdir, "%(playlist_index)03d_%(id)s" if multi
                       else "001_%(id)s")
    args = ["--skip-download", "--write-subs", "--sub-langs", "all", "--sub-format", "srt",
            "-o", tpl, "--no-simulate"]
    if os.path.exists(COOKIE_FILE):
        args += ["--cookies", COOKIE_FILE]
    if items:
        args += ["--playlist-items", items]
    args.append(url)
    rc, out, err = ytdlp(args)
    found = sorted(
        os.path.join(workdir, f) for f in os.listdir(workdir)
        if f.endswith(".srt") and "danmaku" not in f.lower())
    return found, err


# ---------------------------------------------------------------- SRT

CJK = re.compile(r"[㐀-鿿　-〿＀-￯]")
TAG = re.compile(r"<[^>]+>")


def smart_join(a, b):
    """中文之间不加空格，英文之间加。"""
    if not a:
        return b
    if not b:
        return a
    return a + (" " if not (CJK.search(a[-1]) or CJK.search(b[0])) else "") + b


def parse_srt(text):
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = [ln for ln in block.strip().split("\n")]
        idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if idx is None:
            continue
        m = re.match(
            r"\s*(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", lines[idx])
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        body = " ".join(TAG.sub("", ln).strip() for ln in lines[idx + 1:])
        body = re.sub(r"\s+", " ", body).strip()
        if body:
            cues.append({"start": start, "end": end, "text": body})
    return cues


def dedupe(cues):
    """去掉 B 站滚动字幕的重复：后一条带着前一条的尾巴时只保留增量。

    真实数据里有两种形态，这个方法两种都要吃得住：
      - 累积式：大家好 / 大家好我是 / 大家好我是小明
      - 连续式：每条都是独立片段（不以前一条开头），原样保留
    只在前一条够长时才做前缀剥离，避免"好"→"好的"这种短句被误吃。
    """
    out, prev_full = [], ""
    for c in cues:
        t = c["text"]
        if t == prev_full:
            continue
        piece = t
        if len(prev_full) >= 4 and t.startswith(prev_full):
            piece = t[len(prev_full):].strip()
            prev_full = t
            if not piece:
                continue
        else:
            prev_full = t
        out.append({"start": c["start"], "end": c["end"], "text": piece})
    return out


def paragraph(cues, pause=DEFAULT_PAUSE, max_chars=DEFAULT_MAX_CHARS):
    """断段：停顿超阈值 或 累积字数超上限，哪个先到就断。

    ⚠ 别改成只看停顿：实测 B 站 AI 字幕 103 条 cue 里 101 个间隔是 0
    （无停顿的连续流），只看停顿会把整条视频粘成一块几千字的墙。
    字数上限是这类内容唯一可靠的断段信号。
    """
    paras, cur = [], None
    for c in cues:
        if cur is None:
            cur = {"t": c["start"], "end": c["end"], "text": c["text"]}
            continue
        gap = c["start"] - cur["end"]
        joined = smart_join(cur["text"], c["text"])
        if gap > pause or len(joined) > max_chars:
            paras.append(cur)
            cur = {"t": c["start"], "end": c["end"], "text": c["text"]}
        else:
            cur["text"] = joined
            cur["end"] = c["end"]
    if cur:
        paras.append(cur)
    return paras


def parse_items(spec):
    """展开 --playlist-items 这种区间：'3'→{3} / '3-5'→{3,4,5} / '1,3,5'→{1,3,5}"""
    out = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def strip_page_param(url):
    """把 URL 里的 ?p=N 摘掉 —— 分P 一律改用 --playlist-items 表达。

    这两个不能同时给：URL 带 ?p=4 时 yt-dlp 会把这条当成**单个视频**处理，
    playlist_index 变成 None，%(playlist_index)03d 渲染成 NA，文件名就成了
    NA_BVxxx_p4.srt —— P 号从文件名里彻底消失。
    """
    parts = urllib.parse.urlsplit(url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if k != "p"]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q)))


def page_of(path):
    """从文件名认 P 号。两个来源，主备各一：

      1. 序号前缀 `004_...`（--playlist-items 走的正常路径）
      2. id 里的 `_p4` 后缀（多P 视频 yt-dlp 一定加；单P 没有，所以它只在多P 有意义）

    认不出来返回 None —— 绝不返回 1。上面那条 NA 事故就是"猜成 1"造成的。
    """
    base = os.path.basename(path)
    m = re.match(r"(\d+)_", base)
    if m:
        return int(m.group(1))
    m = re.search(r"_p(\d+)\.", base)
    if m:
        return int(m.group(1))
    return None


def fmt_ts(sec):
    s = int(sec)
    h, m, ss = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{ss:02d}" if h else f"{m:02d}:{ss:02d}"


# ---------------------------------------------------------------- 渲染

def pick_lang(srt_paths):
    """从一堆 <序号>_<id>.<lang>.srt 里挑一条：中文人工 > 中文 AI > 其它。"""
    cands = []
    for p in srt_paths:
        m = re.search(r"\.([A-Za-z0-9-]+)\.srt$", os.path.basename(p))
        if m:
            cands.append((m.group(1), p))
    if not cands:
        return None, None

    def rank(lan):
        low = lan.lower()
        is_ai = low.startswith("ai")
        is_zh = "zh" in low or low.endswith("-cn") or low == "zh"
        return {(False, True): 0, (True, True): 1}.get((is_ai, is_zh), 2)

    cands.sort(key=lambda c: rank(c[0]))
    return cands[0]


def lang_label(lan):
    is_ai = lan.lower().startswith("ai")
    src = "AI 自动生成" if is_ai else "UP 上传（人工）"
    return f"{src} · {LANG_NAMES.get(lan, lan)}"


def safe_name(name, limit=80):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    return name[:limit].rstrip(" .") or "未命名"


def build_header(title, uploader, bvid, page=None, total=None, lan=None,
                 series=None, url=None):
    """笔记的文件头。由脚本生成、agent 原样粘贴 —— 不要让 agent 自己拼。

    `- BV：` 和 `- 分P：` 两行是 resolve_path() 认领旧文件的依据（多P 共用一个 BV，
    不连 P 号一起比就会把 P4 的笔记写进 P3）。agent 手拼的话这两行迟早写歪，
    代价是静默覆盖掉另一集。
    """
    lines = [f"# {title}", ""]
    lines.append(f"- UP主：{uploader or '未知'}")
    lines.append(f"- BV：{bvid}")
    if series:
        lines.append(f"- 系列：{series}")
    if page:
        lines.append(f"- 分P：P{page}" + (f" / 共 {total} P" if total else ""))
    lines.append(f"- 日期：{datetime.date.today().isoformat()}")
    lines.append(f"- 字幕：{lang_label(lan)}")
    if url:
        lines.append(f"- 原片：[点这里]({url})")
    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def build_transcript(srt_path, bvid, page=None, cfg=None):
    """把 srt 洗成稿子：去滚动重复 → 切段 → 每段挂一条现成的可点链接。

    返回 (稿子正文, 统计)。稿子是**给 agent 读的原料**，不是成品 ——
    agent 要按主题重新分节、把口语改写成要点，保留这里给出的链接原样不动
    （时间戳 mm:ss 换算成秒很容易算错，所以链接由脚本拼好，agent 只管搬运）。
    """
    lan, path = pick_lang(srt_path)
    if not path:
        return None, None
    with open(path, encoding="utf-8") as f:
        cues = parse_srt(f.read())
    if not cues:
        return None, None
    cfg = cfg or {}
    clean = dedupe(cues)
    paras = paragraph(clean,
                      pause=cfg.get("pause_threshold", DEFAULT_PAUSE),
                      max_chars=cfg.get("max_para_chars", DEFAULT_MAX_CHARS))

    lines = []
    for p in paras:
        page_q = f"p={page}&" if page else ""
        url = f"https://www.bilibili.com/video/{bvid}?{page_q}t={int(p['t'])}"
        lines.append(f"[{fmt_ts(p['t'])}]({url}) {p['text']}")
        lines.append("")
    body = "\n".join(lines).rstrip() + "\n"
    stats = {"lang": lan, "cues": len(cues), "after_dedupe": len(clean),
             "paras": len(paras), "chars": len(body)}
    return body, stats


# ---------------------------------------------------------------- 落盘

def resolve_path(outdir, stem, bvid, page=None):
    """同 BV 重跑 → 覆盖原来那份；异 BV 撞同名 → 退化成 <标题>-<BV>.md

    多P 的所有 P 共用同一个 BV，所以必须连 P 号一起比 —— 否则 P4 会认领 P3 的文件
    把它原地覆盖掉。
    """
    plain = os.path.join(outdir, f"{safe_name(stem)}.md")

    def matches(path):
        try:
            head = open(path, encoding="utf-8").read(2000)
        except OSError:
            return False
        if not re.search(rf"^- BV：{re.escape(bvid)}\s*$", head, re.M):
            return False
        if page:
            return re.search(rf"^- 分P：P{page}[ /]", head, re.M) is not None
        return re.search(r"^- 分P：", head, re.M) is None

    if os.path.exists(plain) and matches(plain):
        return plain, "overwrite"
    for f in sorted(os.listdir(outdir)):
        if f.endswith(".md") and matches(os.path.join(outdir, f)):
            return os.path.join(outdir, f), "overwrite"
    if os.path.exists(plain):
        return os.path.join(outdir, f"{safe_name(stem)}-{bvid}.md"), "renamed"
    return plain, "new"


# ---------------------------------------------------------------- 子命令

def cmd_set_output_dir(args):
    path = os.path.abspath(args.path)
    os.makedirs(path, exist_ok=True)
    cfg = load_config()
    cfg["output_dir"] = path
    save_config(cfg)
    print(json.dumps({"status": "ok", "output_dir": path}, ensure_ascii=False))
    return 0


def cmd_list(args):
    multi, pages, info = probe(args.url)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(args.url).query)
    sel = int(q["p"][0]) if q.get("p") and q["p"][0].isdigit() else None
    out = {
        "status": "ok",
        "kind": "multi" if multi else "single",
        "title": info["title"], "bvid": info["bvid"],
        "uploader": info["uploader"], "count": info["count"],
    }
    if multi:
        out["pages"] = pages
        if sel:
            out["selected"] = sel
    print(json.dumps(out, ensure_ascii=False))
    return 0


def cmd_run(args):
    """抓取 + 清洗，把稿子交给 agent。**这里不写笔记** —— 笔记是 agent 读稿子写出来的。"""
    cfg = load_config()
    outdir = args.out_dir or cfg.get("output_dir")
    if not outdir:
        die("还没设过输出目录。先问用户想把 md 存哪，再跑 set-output-dir。",
            status="need_config", code=2)
    os.makedirs(outdir, exist_ok=True)

    if not os.path.exists(COOKIE_FILE):
        die("没有登录态，B 站未登录拿不到字幕。先跑 SKILL.md 第 0 步的登录。",
            status="need_login", code=3)

    multi, pages, info = probe(args.url)
    bvid = info["bvid"]
    # 链接自带 ?p=N 就等于已经选好了，不再问。只在多P 时认它 ——
    # 单P 链接上的 p 是噪音，拿去喂 --playlist-items 可能选出空集。
    q = urllib.parse.parse_qs(urllib.parse.urlparse(args.url).query)
    if multi and not args.pages and q.get("p") and q["p"][0].isdigit():
        args.pages = q["p"][0]

    if multi and not args.pages:
        die("这是多 P 视频，得先选一集。",
            status="need_page_choice", code=4,
            title=info["title"], bvid=bvid, count=info["count"], pages=pages)

    shutil.rmtree(WORK, ignore_errors=True)   # 清掉上一批，.work 里只留当前这次
    os.makedirs(WORK, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="bili_note_")
    try:
        # 喂给 yt-dlp 的 URL 一律摘掉 ?p= —— 分P 只由 --playlist-items 表达
        srts, err = fetch_subs(strip_page_param(args.url), workdir, args.pages, multi=multi)

        if not srts:
            die("这条视频没有可提取的字幕（接口没返回任何字幕轨）。"
                "如果你确信它有，多半是登录态失效了，先跑 login.py --status 确认。",
                status="no_subtitle", code=5,
                bvid=bvid, title=info.get("title"))

        total = (info.get("count") or len(pages)) if multi else None
        by_index, unknown = {}, []
        for p in srts:
            idx = page_of(p)
            if idx is None:
                unknown.append(os.path.basename(p))
                continue
            by_index.setdefault(idx, []).append(p)
        # 认不出 P 号就停 —— 绝不猜成 1。猜错不是"少一集"，是覆盖掉另一集的笔记。
        if unknown or not by_index:
            die("认不出这些字幕文件属于哪一集：" + ("、".join(unknown) or "（一个都没解析出来）")
                + "。拒绝猜 P 号。",
                status="page_mismatch", code=6, bvid=bvid)

        warnings = []
        if multi and args.pages:
            want, got = parse_items(args.pages), set(by_index)
            extra = got - want           # 多出来的 = 串号了，危险，停
            if extra:
                die(f"要的是 P{sorted(want)}，却多出来 P{sorted(extra)} —— P 号对不上，"
                    f"写下去会覆盖别的笔记，所以停在这里。",
                    status="page_mismatch", code=6, bvid=bvid)
            missing = want - got         # 少了的 = 那一集本来就没字幕，不危险
            if missing:
                warnings.append(
                    f"P{sorted(missing)} 没拿到字幕（多半是那一集本身没有），"
                    f"其余 {len(got)} 集正常。")

        results = []
        for idx in sorted(by_index):
            ent = next((p for p in pages if p.get("index") == idx), None)
            part = (ent or {}).get("title") or ""
            if multi:
                # 多P 的文件名带 P 号，194 集落一个目录才排得开、认得出来
                page = idx
                title = part or f"P{page}"
                stem = f"P{page:03d} {part}".strip() or f"P{page:03d}"
                series = info["title"]
            else:
                page = None
                title = stem = info["title"] or "未命名"
                series = None

            body, stats = build_transcript(by_index[idx], bvid, page=page, cfg=cfg)
            if body is None:
                continue

            src_url = f"https://www.bilibili.com/video/{bvid}" + (f"?p={page}" if page else "")
            header = build_header(title, info.get("uploader"), bvid, page=page,
                                  total=total, lan=stats["lang"], series=series, url=src_url)
            draft = os.path.join(WORK, f"{safe_name(stem)}.md")
            with open(draft, "w", encoding="utf-8", newline="\n") as f:
                f.write(body)
            save_to, how = resolve_path(outdir, stem, bvid, page=page)
            results.append({
                "page": page, "title": title, "stem": stem,
                "draft": draft, "save_to": save_to, "how": how,
                "header": header, "stats": stats,
            })

        if not results:
            die("字幕文件下到了但解析出来是空的，可能是 SRT 格式变了。",
                status="parse_failed", code=1)

        out = {"status": "ok", "bvid": bvid, "results": results}
        if warnings:
            out["warnings"] = warnings
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(prog="bili_note")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("set-output-dir")
    a.add_argument("path")
    a.set_defaults(fn=cmd_set_output_dir)

    b = sub.add_parser("list")
    b.add_argument("url")
    b.set_defaults(fn=cmd_list)

    c = sub.add_parser("run")
    c.add_argument("url")
    c.add_argument("--pages", help="分P 区间，如 3 或 3-5")
    c.add_argument("--out-dir")
    c.set_defaults(fn=cmd_run)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
