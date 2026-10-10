# claude-skills

我自己的 Claude Code skills 合集，一个 skill 一个目录。

| skill | 干什么 |
|---|---|
| [bili-note](bili-note/) | B 站视频链接进 → 一份知识点笔记出。抓字幕、去滚动重复、按时序切段由脚本做；哪些是知识点、怎么归类由 agent 判断 |

## 怎么用

把要用的 skill 目录整个拷进 Claude Code 的 skills 目录（目录名必须与 `SKILL.md` 里的 `name` 一致）：

```bash
git clone <本仓库>
cp -r bili-note ~/.claude/skills/          # Windows 拷到 C:\Users\<你>\.claude\skills\
```

依赖、登录方式、第一次怎么跑，看各 skill 目录下的 README 与 SKILL.md。

## 为什么有些东西不在仓库里

`.gitignore` 排掉了各 skill 的**登录态、本机配置和运行时产物**：

| 被排掉 | 是什么 |
|---|---|
| `cookies.txt` | 登录凭据（bili-note 的 B 站登录态） |
| `config.json` | 本机路径配置，各人不同（照 `config.example.json` 生成） |
| `.work/` | 中间稿，每次运行清空 |
| `bin/yt-dlp.exe` | 17.8MB 第三方二进制，需要时从官方 releases 下同名文件覆盖 |

缺的东西各 skill 的 README 里都写了怎么补。
