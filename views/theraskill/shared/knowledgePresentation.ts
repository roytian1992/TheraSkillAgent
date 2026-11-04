export function knowledgeLabel(value: string, locale: 'zh' | 'en') {
  const names: Record<string, [string, string]> = {
    method_label_or_pattern: ['认知模式', 'Cognitive pattern'],
    style_example: ['回应示例', 'Response example'],
  }
  return names[value]?.[locale === 'zh' ? 0 : 1] || value.replaceAll('_', ' ')
}

export function knowledgePreview(value: string | undefined, label = '', limit = 220) {
  if (!value) return ''
  let text = value.trim()
  if (label && text.startsWith(label)) text = text.slice(label.length).trim()
  // Older indexes append serialized source examples to the readable passage.
  const sourceData = text.search(/\[\s*\{\s*"(?:source_id|release_unit_id|retrieval_id)"\s*:/)
  if (sourceData >= 0) text = text.slice(0, sourceData)
  text = text.replace(/\s+/g, ' ').trim()
  return text.length > limit ? text.slice(0, limit).trimEnd() + '…' : text
}
