# Changelog

显著变更记录于此；格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [3.0.0] - 2026-10-28

### 变更
- 重构图片参数：图片保留与图片任务两组选项自由组合，缺省按文件类型处理
- 新增自定义图片任务：工作目录放任务文件，或命令行直接写提示词
- 优化配置与缓存体验，修复若干问题

## [2.0.2] - 2026-10-24

### 变更
- 图片转写提示词优化，文档内容治理

## [2.0.1] - 2026-10-22

### 新增
- 可运行示例：库直调（examples/quickstart.py）与同步 API 封装（examples/api.py）

## [2.0.0] - 2026-10-19

### 变更
- 重构产出组成：正文、图片链接、内嵌、转写、描述按需组合，缺省仅提取正文

## [1.0.2] - 2026-10-15

### 修复
- 文档修正：README 与库使用文档中的表述错误

## [1.0.1] - 2026-10-15

首个公开发布。

### 新增
- 38 种格式 → 信息完整的 Markdown：正文逐字保留，每张图片带公开链接与转写文本
- 四种使用形态：CLI、Python 库、Claude Code skill、Web 演示


[2.0.2]: https://github.com/kie0519/omnimd/compare/v2.0.1...v2.0.2
[2.0.1]: https://github.com/kie0519/omnimd/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/kie0519/omnimd/compare/v1.0.2...v2.0.0
[1.0.2]: https://github.com/kie0519/omnimd/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/kie0519/omnimd/releases/tag/v1.0.1
