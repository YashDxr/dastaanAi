import { apiFetch } from '@daastaan/api-types'

export interface ApiStory { id: string; title: string | null; status: string; current_version_id: string | null }

/** The real story index endpoint. */
export const listStories = () => apiFetch<ApiStory[]>('/stories')
