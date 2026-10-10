# bili-note

B 站视频链接进 → 一份**知识点笔记**出。

- 给 agent 看的说明书：[SKILL.md](SKILL.md) —— 工作流分工、退出码分流、落盘规则、维护方法都在这里
- 给人看的实现原理与踩坑记录：[how-it-works.html](how-it-works.html) —— 浏览器直接打开

## 装

1. 把本目录拷到 Claude Code 的 skills 目录：
   `~/.claude/skills/bili-note/`（Windows：`C:\Users\<你>\.claude\skills\bili-note\`）
2. 放抓取引擎（仓库里**没有**，17.8MB 二进制不进货）：
   下载 <https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe> 存成 `bin/yt-dlp.exe`
3. 需要 Python 3；脚本只用标准库，无第三方依赖
4. 第一次用先扫码登录（登录态写进 `cookies.txt`，**那是凭据，别提交**）：
   ```bash
   py -3 scripts/login.py
   ```
5. 设一次笔记输出目录（会生成 `config.json`）：
   ```bash
   py -3 scripts/bili_note.py set-output-dir "<你想存笔记的目录>"
   ```

之后对 Claude 说「用 bili-note 抓这个链接：\<B站链接\>」就行。

## 仓库里没有的运行时文件

| 文件 | 为什么不在 | 怎么补 |
|---|---|---|
| `cookies.txt` | 登录凭据 | 跑 `scripts/login.py` 扫码 |
| `config.json` | 本机输出路径，各人不同 | 跑 `set-output-dir`，或照 `config.example.json` 写 |
| `.work/` | 中间稿，每次运行清空 | 运行时自动生成 |
| `bin/yt-dlp.exe` | 第三方二进制，太大 | 见上面第 2 步；B 站改版时同样换官方新版覆盖 |

## 可调参数

`config.json` 里两个可选键，只影响稿子的段落粗细（进而影响笔记的断点），默认值即 `config.example.json` 里那两个：

- `pause_threshold`（默认 1.5）—— 停顿超过这么多秒就断段
- `max_para_chars`（默认 150）—— 单段累积到这个字数就断段；嫌断点太碎就调大
