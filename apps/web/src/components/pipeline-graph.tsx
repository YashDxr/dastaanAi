import type { JobStatus } from '../services/studio'
import { PipelineNode } from './pipeline-node'
const stages = [['understand', 'Story Understanding'], ['registry', 'Character Registry'], ['dialogue', 'Dialogue Split'], ['emotion', 'Emotion Detection'], ['persona', 'Narrator Persona'], ['voice', 'Voice Generation'], ['music', 'Music Generation'], ['assembly', 'Assembly'], ['episode', 'Episode']] as const
export function PipelineGraph({ status }: { status: Record<string, JobStatus> }) { return <section className="pipeline-graph"><div className="pipeline-grid">{stages.map(([id, label]) => <PipelineNode key={id} label={label} status={status[id] ?? 'pending'} />)}</div><p>{stages.length - 1} connections · {stages.length} stages · streaming</p></section> }
