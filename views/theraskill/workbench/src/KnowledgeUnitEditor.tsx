import KnowledgeEntry from '../../shared/KnowledgeEntry'

export default function KnowledgeUnitEditor({ id, indexId, checksum, locale, onBack, onDirtyChange }: { id: string; indexId?: string; checksum?: string; locale: 'zh' | 'en'; onBack: () => void; onDirtyChange?: (dirty: boolean) => void }) {
  return <section className="knowledge-unit-editor">
    <div className="workspace-toolbar"><button className="back-button" onClick={onBack}>← {locale === 'zh' ? '返回知识与索引' : 'Back to Knowledge & indexes'}</button><span className="workspace-status">{locale === 'zh' ? '知识内容与修订' : 'Knowledge content and revisions'}</span></div>
    <KnowledgeEntry key={[id,indexId,checksum].join(':')} id={id} indexId={indexId} checksum={checksum} locale={locale} workspace onDirtyChange={onDirtyChange} />
  </section>
}
