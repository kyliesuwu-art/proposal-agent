# 方案知识库项目

## 项目背景

康晋电气内部 demo，供方案部门试用。公司有大量历史电气工程方案（PPTX 格式），目前靠人工翻找。目标是把这些方案入库，支持语义检索，并辅助生成新方案。

定位：内部 demo 优先，不是生产系统。不需要多用户、权限管理、高并发。优先跑通核心流程，代码够用即可。

## 当前阶段
入库阶段已完成（文字 + 图片 + 版本控制），生成阶段已基本完成（检索+生成全链路跑通、引用防幻觉已上线），剩余：方案类型过滤 bug 修复 + Markdown 文件持久化。

详见 [DECISIONS.md](DECISIONS.md) — 开发路线图、项目状态、已知问题详情。

## 技术栈
- 文档解析：MinerU云API（见 docs/mineru_api.md）
- 向量库：Chroma（本地）
- 生成：DashScope Qwen（qwen3.7-max，与 embedding 同一供应商），通过 adapters/llm_client.py 调用
- 输出格式：Markdown

## 项目结构
Core/
  claude.md         # 本文件，架构说明与编码约定
  DECISIONS.md      # 开发路线图、项目状态、已知问题详情、架构决策
  docs/             # 参考文档（API 规范、数据格式，供开发参考）
  files/            # 待解析的本地 PPTX 文件（运行时输入，不提交 git）
  src/
    adapters/
      parser.py         # MinerU 云 API 封装
      vector_store.py   # Chroma 封装
      llm_client.py     # LLM API 封装（可换模型）
    main.py

## 数据流
PPTX 文件
  → parser.py（MinerU 解析，同时提取图片到本地 images/ 目录）
  → list[SlideDict]  每个 slide: {slide_number, title, content, source_file, images}
     images: [{"path": 本地图片路径, "caption": 图片描述（可能为空）}]
  → vector_store.py（Chroma 入库，按 file_hash 判断版本、images 序列化存 metadata）
  → 检索时返回相关 slide（含文字 + 关联图片素材）
  → llm_client.py（生成新方案段落）

## 关键设计决策
- 按 slide 切分：每张幻灯片作为一个独立的 Chroma document，不合并整份文件
- adapters 层可替换：MinerU / Chroma / LLM 都封装在 adapters 下，外部代码不直接 import 这些库
- 单一入口：main.py 是唯一的业务逻辑入口，adapters 只做 IO 封装
- **图片提取但不强制要求 caption**：图片文件统一提取到本地 `images/<pptx文件名>/` 目录，不管 MinerU 有没有识别出 caption 都保留；只有 caption 非空时才拼进正文参与语义检索
- **版本控制用文件哈希**：ingest 前先算 pptx 的 MD5，跟 Chroma 里记录的上次入库哈希比对——一致就跳过，不一致才重新解析。解析成功后才清空旧版本、插入新版本（不是先删再解析）
- **DashScope embedding 分批 + 重试**：单次请求最多 10 条文本，自动按 10 条分批调用，批次间隔 0.2s 防限流，遇到 429/5xx 自动重试最多 3 次
- **debug_zips/ 按源文件名保存**：MinerU 返回的原始 zip 包保存到 `debug_zips/<pptx文件名>.zip`，方便排查解析问题

## MinerU 接入关键决策
- `is_ocr: False`：PPTX 文字是矢量的，不需要 OCR
- `base_url` 单独存：所有接口共享同一个前缀，换环境或域名只改一个地方
- 上传用二进制模式 `"rb"`：PPTX 是二进制格式，文本模式读取会损坏文件
- 按 `page_idx` 分组，不用 Markdown 分隔符：MinerU 对 PPTX 输出的 Markdown 是连续的（标题用 "##"，没有 "---" 分隔符），按 page_idx 分组稳定

完整 API 规范（模型版本、额度、调用模式）见 [docs/mineru_api.md](docs/mineru_api.md)。

## 编码规范
- 可替换依赖封装在 /src/adapters/ 下
- 框架和标准库直接 import
- adapters 外的代码不直接 import 向量库/LLM/解析库
- 复杂逻辑处加中文注释说明意图
- 每个 class 写一行中文 docstring 说明职责
- 函数参数和返回值写类型标注

## 环境
- Python 环境：标准 venv
- LLM 调用走阿里云 DashScope 代理（兼容 OpenAI 接口）
- MinerU API Key 从环境变量 MINERU_API_KEY 读取（实际代码用 MINERU_TOKEN）

## 已知问题索引（代码位置）

| # | 问题 | 涉及文件 | 关键行 |
|---|------|----------|--------|
| 1 | 表格解析潜在数据丢失 | `parser.py` | 253-260 |
| 3 | 方案类型过滤失效 | `vector_store.py` + `pipeline.py` | 409 + 230-239 |
| 4 | 图片描述质量 | `parser.py` | 262-275 |
| 7 | PDF 解析支持 | `parser.py` | 全文件 |

问题详情（含现象、待办、优先级）见 [DECISIONS.md](DECISIONS.md)。

## 检索策略速查

- Query 改写：需求描述 → 1-3 个检索用 query（LLM）
- metadata 过滤：按 proposal_type 圈定范围（当前有精确匹配 bug，见 #3）
- 向量检索：纯语义（Chroma），取候选
- 相邻 slide 扩展：命中 slide 前后各带 1-2 张
- Hybrid 检索 / Rerank：暂不做，验证后再加

## 架构决策速记

- v1 生成输出为结构化 IR 对象（不掺 pptx/docx 代码），薄导出函数渲染
- v3 换皮方案：用 python-pptx/lxml 直接复制源 pptx 对应 slide 的 XML 对象到新 presentation，位置/配色/形状随 XML 原样保留，仅替换文本和图片
- 测算模块价值边界：需明确 (a) 从 RAG 历史项目预填参数 或 (b) 测算结果自动写回生成大纲

以上决策的完整讨论见 [DECISIONS.md](DECISIONS.md)。
