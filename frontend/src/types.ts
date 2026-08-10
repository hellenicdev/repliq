export type JobStatus = 'processing' | 'completed' | 'failed'

export interface Clip {
  videoId: string
  videoTitle: string
  character: string
  text: string
  startTime: number
  endTime: number
  score: number
}

export interface Job {
  _id: string
  status: JobStatus
  sentence: string
  clips: Clip[]
  outputUrl: string | null
  message: string | null
  error: string | null
  createdAt: string
  completedAt: string | null
}

export interface Video {
  _id: string
  title: string
  source: string
  fileUrl: string
  duration: number
  width: number
  height: number
  fps: number
}
