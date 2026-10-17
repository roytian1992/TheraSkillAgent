你要把一组不在选项表里的 client state dimension 归一化到给定选项。

输入中的 `old_dimension` 是错误或过细的标签，`state_label` 是该状态的中文描述。
只能选择 `allowed_dimensions` 中的一个值作为 `new_dimension`。

不要新增选项，不要输出解释性正文。

选择原则：
- 主诉、困扰主题、症状持续时间、诊断/治疗史、学习/职业/生活问题 -> `presenting_problem` 或 `life_context`
- 情绪体验、恐惧、压力感 -> `emotion`
- 自我评价、信念、担忧、认知理解、兴趣偏好、价值判断 -> `cognition`
- 行为模式、沟通行为、注意力表现、睡眠作息行为 -> `behavior`
- 身体症状、睡眠、医疗史、药物治疗、幻听等身心/医学线索 -> `somatic` 或 `risk_signal`
- 人际关系、沟通模式、家庭/婚恋互动 -> `interpersonal_context`
- 人生背景、学业职业、资源优势、兴趣、经济/居住/抚养等背景 -> `life_context`
- 求助动机、准备度、需要、行动意愿 -> `motivation_readiness`
- 自伤、自杀、他伤、精神病性体验、严重自我忽视、虐待暴力等风险线索 -> `risk_signal`

如果多个都可能，选对后续治疗动作最有用的上位维度。

输出 JSON：

{
  "mappings": [
    {
      "old_dimension": string,
      "state_label": string,
      "new_dimension": string,
      "reason": string
    }
  ]
}

allowed_dimensions:
__ALLOWED_DIMENSIONS__

items:
__ITEMS__
