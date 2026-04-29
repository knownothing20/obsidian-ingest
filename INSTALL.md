# INSTALL.md — obsidian-ingest 安装指南

## 前置条件

1. **Python 3.10+** — 系统已安装
2. **Obsidian** — 已安装，有一个 Vault
3. **MinerU Token** — 从 [mineru.net](https://mineru.net) 免费注册获取

## 一、安装

```bash
git clone https://github.com/YOUR_USERNAME/obsidian-ingest.git
cd obsidian-ingest
pip install -r requirements.txt
cp config.yaml.example config.yaml
```

## 二、配置

编辑 `config.yaml`：

```yaml
# 必填：你的 Obsidian Vault 路径
vault:
  root: "D:/MyObsidianVault"    # Windows
  # root: "~/MyVault"           # macOS/Linux

# 必填：MinerU Token
tokens:
  - token: "YOUR_TOKEN_HERE"
    expires: "2026-12-31T23:59:59+08:00"
```

## 三、初始化 Vault 目录

```bash
python scripts/cli.py init --vault "D:/MyObsidianVault"
```

自动创建：

```
你的 Vault/
├── raw/
│   ├── todo/           ← PDF 放这里
│   ├── processing/     ← 自动
│   └── 09-archive/     ← 自动归档
├── wiki/
│   ├── sources/        ← 自动生成的来源摘要
│   ├── concepts/       ← 概念页
│   ├── entities/       ← 实体页
│   ├── syntheses/      ← 综合报告
│   ├── logs/           ← 操作日志
│   └── index.md        ← 索引
├── notes/              ← 你的笔记（AI 不动）
├── assets/             ← 图片附件
└── SCHEMA.md           ← 规则定义
```

## 四、Obsidian 设置

打开 Obsidian → 设置 → 文件与链接：

1. **内部链接格式** → 选「相对路径」
2. **自动更新内部链接** → 开启
3. **新建笔记位置** → 可设为 `wiki/sources`（可选）

## 五、使用

```bash
# 放入 PDF
cp 学习资料/*.pdf "D:/MyObsidianVault/raw/todo/"

# 一键编译
python scripts/cli.py compile --vault "D:/MyObsidianVault"

# 预览（不执行）
python scripts/cli.py compile --vault "D:/MyObsidianVault" --dry-run

# 查看状态
python scripts/cli.py status --vault "D:/MyObsidianVault"

# 监听模式
python scripts/cli.py watch --vault "D:/MyObsidianVault"
```

## 六、Docker

```bash
docker run -d \
  -v "D:/MyObsidianVault:/vault" \
  -v ./config.yaml:/app/config.yaml:ro \
  obsidian-ingest
```

## 七、旧格式兼容（DOC/PPT）

MinerU 仅支持 DOCX/PPTX，旧格式 `.doc`/`.ppt` 需要先转换为新格式。

### 支持的后端

| 后端 | 平台 | 转换 .doc | 转换 .ppt |
|------|------|-----------|----------|
| MS Office COM | Windows | ✅ Word | ✅ PowerPoint |
| WPS COM | Windows | ✅ WPS Writer | ✅ WPS Presentation |
| LibreOffice headless | 跨平台 | ✅ | ✅ |

**后端探测机制**：运行时自动检测已安装的后端，按优先级选择（MS Office > WPS > LibreOffice），无需手动配置。

查看探测结果：

```bash
python scripts/legacy_converter.py --check
```

### 安装后端（任选其一）

**Microsoft Office**（推荐，已装则无需操作）

官网：https://www.office.com/

**WPS Office**

官网：https://www.wps.cn/

```powershell
winget install Kingsoft.WPSOffice
```

**LibreOffice**

官网：https://www.libreoffice.org/download/download/

```powershell
winget install TheDocumentFoundation.LibreOffice
# 或
scoop bucket add extras && scoop install libreoffice
# 或
choco install libreoffice-fresh
```

安装任意一个后，重新运行 `compile` 即可，无需额外配置。

## 八、插件兼容性

已测试兼容：
- ✅ Dataview — front matter 字段可查询
- ✅ Graph View — 双链自动显示
- ✅ Templater — 不冲突
- ✅ QuickAdd — 可配合使用
- ✅ Tag Wrangler — tags 字段兼容

## 八、故障排除

### Token 过期
```bash
python scripts/cli.py status
# 查看 Token 剩余时间
```

### 处理中断
```bash
# 自动恢复
python scripts/cli.py resume --vault "D:/MyObsidianVault"
```

### 中文乱码
确保终端编码为 UTF-8：
```bash
# Windows PowerShell
chcp 65001
```
