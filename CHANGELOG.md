# Changelog

显著变更记录于此；格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [2.0.2] - 2026-10-24

### 变更
- 图片转写提示词优化，文档内容治理

## [2.0.1] - 2026-10-22

### 新增
- 可运行示例：库直调（examples/quickstart.py）与同步 API 封装（examples/api.py）

## [2.0.0] - 2026-10-19

### 变更
- 重新设计产出组成：正文、图片链接、内嵌、转写、描述五个部件按需组合，全不选即仅提取正文
- 仅提取正文成为缺省：不调用模型、不需要任何配置
- 移除图片传入方式配置，由产出组成自动决定

## [1.0.2] - 2026-10-15

### 修复
- 文档修正：README 与库使用文档中的表述错误

## [1.0.1] - 2026-10-15

首个公开发布。

### 新增
- 38 种格式 → 信息完整的 Markdown：正文逐字保留，每张图片带公开链接与转写文本
- 四种使用形态：CLI、Python 库、Claude Code skill、Web 演示
- 双层缓存与内容寻址去重：同配置重跑不调模型不上传，同图只处理一次


[2.0.2]: https://github.com/kie0519/omnimd/compare/v2.0.1...v2.0.2
[2.0.1]: https://github.com/kie0519/omnimd/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/kie0519/omnimd/compare/v1.0.2...v2.0.0
[1.0.2]: https://github.com/kie0519/omnimd/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/kie0519/omnimd/releases/tag/v1.0.1
