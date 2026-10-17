import { useEffect, useState } from 'react'

export type Locale = 'zh' | 'en'

const STORAGE_KEY = 'theraskill-locale'

// Translate only the known application chrome strings; domain content is not
// part of the translation table.
const TRANSLATIONS: Record<string, string> = {
  '过程参考': 'Process guidance', '互动模式': 'Interaction pattern', '支持参考': 'Support guidance',
  '方法参考': 'Method rubric', '边界参考': 'Boundary reference', '参考依据': 'Evidence',
  '没有理解我的意思': "Didn't understand me", '回答不相关': 'Not relevant', '建议不实际': 'Not practical',
  '表达让我不舒服': 'Uncomfortable wording', '事实可能有误': 'Possibly inaccurate',
  '风险处理不合适': 'Inappropriate risk handling', '其他': 'Other', '已记录': 'Recorded',
  '有帮助': 'Helpful', '没帮助': 'Not helpful', '补充说明（可选）': 'Additional notes (optional)',
  '允许内容评审': 'Allow content review', '允许研究使用': 'Allow research use', '允许形成知识候选': 'Allow knowledge derivation',
  '提交': 'Submit', '你': 'You', '关闭': 'Close', '删除这段对话？': 'Delete this conversation?',
  '关闭会话列表': 'Close session list', '新对话': 'New conversation', '会话历史': 'Conversation history',
  '已结束': 'Completed', '生成中': 'Generating', '进行中': 'Active', '删除对话': 'Delete conversation',
  '服务已连接': 'Service connected', '服务未连接': 'Service unavailable', '关闭侧栏': 'Close sidebar',
  '打开会话列表': 'Open session list', '正在回应': 'Responding', '我的记忆': 'My memory',
  '我的反馈': 'My feedback', '导出对话': 'Export conversation', '结束对话': 'End conversation',
  '关闭错误': 'Dismiss error', '今天想聊些什么？': 'What would you like to talk about today?',
  '回应生成失败': 'Response generation failed', '重试': 'Retry', '这段对话已结束': 'This conversation has ended',
  '开始新对话': 'Start new conversation', '输入你想说的内容': 'Type what you want to say', '发送': 'Send',
  '关闭我的反馈': 'Close my feedback', '已申请删除': 'Deletion requested', '撤回': 'Withdraw', '暂无反馈': 'No feedback yet',
  '沟通偏好': 'Communication preference', '已确认目标': 'Confirmed goal', '已确认背景': 'Confirmed context',
  '应对偏好': 'Coping preference', '明确边界': 'Explicit boundary', '不要记住': 'Do not remember',
  '关闭我的记忆': 'Close my memory', '有效天数': 'Valid for (days)', '填写需要跨对话保留的内容': 'Enter content to retain across conversations',
  '敏感内容': 'Sensitive content', '创建待确认项': 'Create proposal', '已启用': 'Active', '待确认': 'Proposed',
  '敏感 · ': 'Sensitive · ', '有效至 ': 'Expires ', '确认启用': 'Approve', '保存': 'Save', '编辑': 'Edit', '删除': 'Delete',
  '暂无记忆': 'No memories yet', '服务': 'Service', 'Support': 'Support',
  '切换到英文': 'Switch to English', '切换到中文': 'Switch to Chinese',
  '查看角色': 'View role', '搜索主题或标签': 'Search topics or tags', '全部类型': 'All types',
  '学员': 'Learner', '教师': 'Instructor', '研究员': 'Researcher', '场景库': 'Scenario library', '训练记录': 'Training history',
  '支持性探索': 'Supportive exploration', '认知重评': 'Cognitive reappraisal', '关系修复': 'Rupture and repair',
  '风险与转介': 'Risk escalation and handoff', 'REBT 信念工作': 'REBT belief work',
  '个人成长': 'Personal growth', '家庭教育': 'Family education', '家庭关系': 'Family relationships', '婚姻咨询': 'Marriage counseling',
  '心理健康': 'Mental health', '心理咨询': 'Counseling', '儿童心理咨询': 'Child counseling', '家庭咨询': 'Family counseling', '人际关系': 'Interpersonal relationships', '教育咨询': 'Education counseling',
  '全部难度': 'All difficulties', '低': 'Low', '中': 'Medium', '高': 'High',
  '全部风险': 'All risk levels', '需关注': 'Needs attention', '更新结果': 'Refresh results',
  '未标注': 'Unlabeled', '低风险': 'Low risk', '病例类别': 'Case category',
  '建议轮次': 'Recommended turns', '轮': 'turns', '风险带': 'Risk band',
  '来访者角色': 'Client role', '称呼': 'Name', '年龄段': 'Age band', '职业': 'Occupation',
  '关系状态': 'Relationship', '训练标签': 'Training tags', '会话准备': 'Session setup',
  '训练角色': 'Training role', '开场角色': 'Opening role', '分配当前场景': 'Assign scenario',
  '训练任务名称': 'Training task name', '学员标识': 'Learner ID', '已分配': 'Assigned',
  '分配任务': 'Assign task', '选择一个训练场景': 'Select a training scenario',
  '返回场景库': 'Back to scenario library', '暂停会话': 'Pause session', '恢复会话': 'Resume session',
  '结束并复盘': 'End and review', '刷新会话': 'Refresh session', '会话已就绪': 'Session ready',
  '标记此回应': 'Flag this response', '发送回应': 'Send response', '训练进度': 'Training progress',
  '场景信息': 'Scenario details', '类别': 'Category', '会话复盘': 'Session review',
  '正在生成复盘': 'Generating review', '复盘生成失败，请稍后重试。': 'Review failed. Please try again.',
  '教师视图': 'Instructor view', '潜在核心冲突': 'Potential core conflicts',
  '渐进披露条件': 'Progressive disclosure conditions', '参考 Persona': 'Reference persona',
  '固定身份': 'Fixed identity', '隐藏事实与披露条件': 'Hidden facts and disclosure conditions',
  '逐轮批注': 'Turn annotations', '选择一个 turn': 'Select a turn', '批注': 'Annotation',
  '替代回应（可选）': 'Alternative response (optional)', '回应比较': 'Response comparison',
  '分析': 'Analysis', '会话反馈': 'Session feedback', '已提交': 'Submitted', '会话评分': 'Session rating',
  '记录体验或问题': 'Describe experience or issue', '关闭标记': 'Close flag', '问题类别': 'Issue category',
  '表现不真实': 'Unrealistic', '回应不恰当': 'Inappropriate response', '前后状态不一致': 'Inconsistent state',
  '风险处理有疑问': 'Risk-handling concern', '提交标记': 'Submit flag',
  '在线生成训练情景': 'Online scenario generation', '输入一个训练主题，生成仅用于研究和演示的 synthetic draft。生成结果会带有可追踪的病例状态，确认后即可开始训练，不会写入正式测试集。': 'Enter a training topic to generate a synthetic draft for research and demonstration. The result includes traceable case state; review it before training. It will not enter the formal test set.',
  '生成 synthetic draft': 'Generate synthetic draft', '正在生成情景…': 'Generating scenario…',
  '草稿已生成。开始训练前请确认场景描述、难度和风险带。': 'Draft generated. Confirm the brief, difficulty, and risk band before training.',
  '生成草稿治理': 'Draft governance', '待审核': 'Pending review', '已批准': 'Approved', '已退回': 'Rejected',
  '质量': 'Quality', '分': 'points', '检查通过': 'Checks passed', '有提醒项': 'Warnings present', '存在阻断问题': 'Blocking issues found',
  '编辑草稿': 'Edit draft', '保存并重新检查': 'Save and recheck', '批准复用': 'Approve for reuse', '退回草稿': 'Reject draft',
  '退回原因': 'Rejection reason', '退回时必填，批准时可留空': 'Required when rejecting; optional when approving',
  '场景描述': 'Scenario brief', '用逗号分隔': 'Separate with commas',
  '批量生成课程场景': 'Batch-generate course scenarios', '课程标题': 'Course title',
  '课程目标或共同训练主题（至少 8 个字）': 'Course goal or shared training topic (at least 8 characters)',
  '生成课程批次': 'Generate course batch',
  '个草稿；课程草案待逐个审核后使用。': ' drafts generated; review each one before using the course draft.',
  '个可用训练场景': 'available training scenarios',
  '请先修改主题。': 'Please revise the topic first.', '退回草稿前请填写原因。': 'Enter a reason before rejecting the draft.',
  '质量检查': 'Quality checks', '重复度': 'Similarity', '批次': 'Batch',
  '开始训练': 'Start training', '取消': 'Cancel',
  '专家知识贡献': 'Expert knowledge contribution', '已进入 candidate review': 'Entered candidate review',
  '不会直接写入生产 knowledge': 'Will not write directly to production knowledge', '提交贡献': 'Submit contribution',
  '反馈审阅': 'Feedback review', '未绑定专家档案': 'No expert profile linked', '刷新队列': 'Refresh queue',
  '审阅队列': 'Review queue', '暂无待审阅项': 'No pending reviews', '选择一条已分配记录': 'Select an assigned record',
  '对话上下文': 'Conversation context', '无用户正文': 'No user message', 'Agent 回应': 'Agent response',
  '无回应正文': 'No assistant response', '路由与证据': 'Routing and evidence',
  '本条记录没有 machine reviewer 结果': 'No machine reviewer result', '专家决策': 'Expert decision',
  '问题归因': 'Issue attribution', '处理动作': 'Actions', '处理通道': 'Remediation lanes',
  '置信度': 'Confidence', '判断依据': 'Rationale', '修订回应': 'Corrected response',
  '回归案例 JSON': 'Regression case JSON', '提交决策': 'Submit decision', '候选治理': 'Candidate governance',
  '暂无候选': 'No candidates', '结构化': 'Structure', '去重检查': 'Dedup check', '回归验证': 'Regression validation',
  '审定依据': 'Adjudication rationale', '创建 preview': 'Create preview', '暂无 release': 'No releases',
  '激活': 'Promote', '回滚': 'Rollback',
}

const REVERSE_TRANSLATIONS = Object.fromEntries(Object.entries(TRANSLATIONS).map(([zh, en]) => [en, zh]))

function translated(value: string, locale: Locale): string {
  const leading = value.match(/^\s*/)?.[0] || ''
  const trailing = value.match(/\s*$/)?.[0] || ''
  const core = value.trim()
  if (!core) return value
  const table = locale === 'en' ? TRANSLATIONS : REVERSE_TRANSLATIONS
  const direct = table[core]
  if (direct) return `${leading}${direct}${trailing}`
  if (locale === 'en') {
    const recent = core.match(/^最近 (\d+) 次训练$/)
    if (recent) return `${leading}${recent[1]} recent training sessions${trailing}`
    const available = core.match(/^(\d+) 个可用训练场景$/)
    if (available) return `${leading}${available[1]} available training scenarios${trailing}`
    const generated = core.match(/^已生成 (\d+) 个草稿；课程草案待逐个审核后使用。$/)
    if (generated) return `${leading}${generated[1]} drafts generated; review each one before using the course draft.${trailing}`
    const batch = core.match(/^批次 (\d+)\/(\d+)$/)
    if (batch) return `${leading}Batch ${batch[1]}/${batch[2]}${trailing}`
    const quality = core.match(/^质量 (.+) 分$/)
    if (quality) return `${leading}Quality ${quality[1]} points${trailing}`
    const similarity = core.match(/^与已有草稿「(.+)」相似度 (.+)，请先修改主题。$/)
    if (similarity) return `${leading}Similarity to draft “${similarity[1]}” is ${similarity[2]}; please revise the topic.${trailing}`
  } else {
    const available = core.match(/^(\d+) available training scenarios$/)
    if (available) return `${leading}${available[1]} 个可用训练场景${trailing}`
    const generated = core.match(/^(\d+) drafts generated; review each one before using the course draft\.$/)
    if (generated) return `${leading}已生成 ${generated[1]} 个草稿；课程草案待逐个审核后使用。${trailing}`
    const batch = core.match(/^Batch (\d+)\/(\d+)$/)
    if (batch) return `${leading}批次 ${batch[1]}/${batch[2]}${trailing}`
    const quality = core.match(/^Quality (.+) points$/)
    if (quality) return `${leading}质量 ${quality[1]} 分${trailing}`
    const similarity = core.match(/^Similarity to draft “(.+)” is (.+); please revise the topic\.$/)
    if (similarity) return `${leading}与已有草稿「${similarity[1]}」相似度 ${similarity[2]}，请先修改主题。${trailing}`
  }
  return value
}

function applyDomLocale(locale: Locale) {
  const root = document.body
  if (!root) return
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const textNodes: Text[] = []
  let current: Node | null
  while ((current = walker.nextNode())) {
    const parent = (current as Text).parentElement
    if (parent && !['SCRIPT', 'STYLE', 'TEXTAREA', 'PRE'].includes(parent.tagName)) textNodes.push(current as Text)
  }
  textNodes.forEach((node) => {
    const next = translated(node.data, locale)
    if (next !== node.data) node.data = next
  })
  root.querySelectorAll<HTMLElement>('[title],[aria-label],input[placeholder],textarea[placeholder]').forEach((element) => {
    for (const attribute of ['title', 'aria-label', 'placeholder']) {
      const value = element.getAttribute(attribute)
      if (value) {
        const next = translated(value, locale)
        if (next !== value) element.setAttribute(attribute, next)
      }
    }
  })
}

export function useLocale() {
  const [locale, setLocaleState] = useState<Locale>(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    return stored === 'en' ? 'en' : 'zh'
  })
  useEffect(() => {
    document.documentElement.lang = locale === 'en' ? 'en' : 'zh-CN'
    window.localStorage.setItem(STORAGE_KEY, locale)
    applyDomLocale(locale)
    const observer = new MutationObserver(() => applyDomLocale(locale))
    if (document.body) observer.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['title', 'aria-label', 'placeholder'] })
    return () => observer.disconnect()
  }, [locale])
  return { locale, setLocale: (next: Locale) => setLocaleState(next), toggleLocale: () => setLocaleState((current) => current === 'zh' ? 'en' : 'zh') }
}
