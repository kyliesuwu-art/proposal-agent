# MinerU 云 API 参考

> 来源：https://mineru.net/apiManage/docs
> 整理日期：2026-06

## 重要：上传是两步，不是一步

MinerU 云端 API 的文件上传流程是"先申请，再上传"，不是直接 POST 文件。

```
Step 1: POST /v1/agent/parse/file  （发 JSON，申请上传位置）
           ← 返回 task_id + file_url（OSS 签名 URL）

Step 2: PUT <file_url>             （把文件二进制直接传到 OSS）
           ← 204 No Content

Step 3: GET /v1/agent/parse/{task_id}  （轮询，等解析完成）
           ← 返回 state + 结果
```

文件不经过 MinerU 服务器本身，直接存到阿里云 OSS。

---

## Step 1：申请上传

**端点**
```
POST https://mineru.net/api/v1/agent/parse/file
```

**Header**
```
Authorization: Bearer <MINERU_API_KEY>
Content-Type: application/json
```

**Body（JSON）**
```json
{
  "file_name": "document.pdf",
  "language": "ch",
  "enable_table": true,
  "is_ocr": false,
  "enable_formula": false
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| file_name | str | 文件名，含扩展名 |
| language | str | 文档语言，中文用 "ch" |
| enable_table | bool | 是否识别表格，默认 true |
| is_ocr | bool | 是否强制 OCR，默认 false |
| enable_formula | bool | 是否识别公式，PPTX 通常设 false |

**返回**
```json
{
  "code": 0,
  "data": {
    "task_id": "a90e6ab6-44f3-4554-b459-b62fe4c6b436",
    "file_url": "https://oss.cn-xxx.aliyuncs.com/...?签名参数"
  },
  "msg": "ok"
}
```

---

## Step 2：上传文件到 OSS

**直接 PUT 文件二进制到 file_url，不加任何 Header（签名已在 URL 里）**

```python
with open(file_path, "rb") as f:
    requests.put(file_url, data=f)
```

返回 HTTP 200 或 204 即为成功。

---

## Step 3：轮询解析状态

**端点**
```
GET https://mineru.net/api/v1/agent/parse/{task_id}
```

**Header**
```
Authorization: Bearer <MINERU_API_KEY>
```

**返回示例（进行中）**
```json
{
  "code": 0,
  "data": {
    "task_id": "a90e6ab6-...",
    "state": "running"
  },
  "msg": "ok"
}
```

**state 取值**
| state | 含义 |
|-------|------|
| waiting-file | 等待文件上传 |
| pending | 排队中 |
| running | 解析中 |
| done | 完成 |
| failed | 失败 |

**返回示例（完成）**
```json
{
  "code": 0,
  "data": {
    "task_id": "a90e6ab6-...",
    "state": "done",
    "full_zip_url": "https://cdn.mineru.net/result/xxx.zip"
  },
  "msg": "ok"
}
```

> ⚠️ 结果是一个 zip 包，里面包含 Markdown 文件和图片。
> 当前阶段只需要 Markdown 文字内容，图片跳过。

---

## 注意事项

- `parse_method: "slide"` 这个参数在云端 API 里**不存在**（是本地版才有的参数，AI 编造的）
- PPTX 支持解析，但结果格式是 Markdown，不是按 slide 分好的 list
- 按 slide 切分需要我们自己解析返回的 Markdown（用 `---` 或 `# ` 分隔符）
- 云端 API 限制：轻量版最多 50 页

---

## 当前代码问题（parser.py 旧版）

| 问题 | 说明 |
|------|------|
| 上传方式错误 | 用了 multipart POST，实际应该两步：JSON POST + PUT |
| URL 端点错误 | 用了 `/v1/parse`，实际是 `/v1/agent/parse/file` |
| parse_method: slide | 云端 API 不支持此参数 |
| 结果解析错误 | 假设返回 `slides` list，实际返回 zip URL |