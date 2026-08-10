import type { Job } from '../types'

const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000'

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  const body = await resp.json().catch(() => null)
  if (!resp.ok) {
    const detail = body?.detail ?? body?.message ?? `Request failed (${resp.status})`
    throw new ApiError(typeof detail === 'string' ? detail : JSON.stringify(detail), resp.status)
  }
  return body as T
}

export interface GenerateResult {
  jobId: string
}

export function createJob(sentence: string, turnstileToken: string | null): Promise<GenerateResult> {
  return request<GenerateResult>('/api/generate', {
    method: 'POST',
    body: JSON.stringify({ sentence, turnstileToken }),
  })
}

export function getJob(jobId: string): Promise<Job> {
  return request<Job>(`/api/jobs/${jobId}`)
}

export function outputUrl(jobId: string): string {
  return `${API_URL}/api/jobs/${jobId}/output`
}

/** Poll a job until it reaches a terminal state. */
export async function waitForJob(
  jobId: string,
  onUpdate: (job: Job) => void,
  intervalMs = 2000,
  maxAttempts = 1600,
): Promise<Job> {
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    const job = await getJob(jobId)
    onUpdate(job)
    if (job.status === 'completed' || job.status === 'failed') return job
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  throw new ApiError('Timed out waiting for the job to finish.', 408)
}
