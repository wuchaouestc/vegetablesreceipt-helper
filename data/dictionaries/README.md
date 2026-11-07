# 手写业务词典

`handwriting.json` 是正式词典，初始为空。没有已确认的业务清单时，不从模型输出或素材草稿自动生成全局规则。空词典不会限制新品类、编码或单位。

词条模板（示例仅说明格式，不代表真实业务；复制到词典后应填写真实值、确认来源和日期）：

```json
{
  "id": "stable-unique-id",
  "field": "category",
  "canonical": "经确认的标准名",
  "aliases": [],
  "scope": {"customers": []},
  "source": "业务负责人确认的来源",
  "confirmed_at": "2026-11-06",
  "enabled": false
}
```

`field` 支持 `category`、`customer_code`、`unit`、`remark`。`customers` 为空表示全局，否则只有明确选择对应客户才可提供候选。数量不做枚举。编码必须是字符串，前导零不得删除。

通过 `handwriting.vocabulary.save_vocabulary()` 保存会先验证，再备份原文件并原子替换。加载失败返回禁用提示。版本升级应更新 revision；缓存还应包含内容哈希。

所有建议与原记录分开，当前没有自动纠错或自动接入打印。候选预览用 `python -m handwriting preview --dictionary ... --records ... --output ...`，records 为使用 category/customer_code/weight/unit/remark 键的 JSON 数组。
