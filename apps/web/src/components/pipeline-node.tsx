import type { JobStatus } from '../services/studio'

export interface PipelineNodeProps { label: string; status: JobStatus }
export function PipelineNode({ label, status }: PipelineNodeProps) { const mark = status === 'completed' ? '✓' : status === 'running' ? '⌁' : status === 'failed' ? '!' : '○'; return <article className={`pipeline-node ${status}`}><div><b>{mark}</b><span>{label}</span></div><small>{status}</small>{status === 'running' && <i className="node-progress" />}</article> }
