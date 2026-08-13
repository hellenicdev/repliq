import { useEffect, useState } from 'react'
import TurnstileWidget, { resetTurnstile } from '../components/TurnstileWidget'
import { ApiError, createJob, outputUrl, waitForJob } from '../services/api'
import type { Clip, Job } from '../types'

type Phase = 'idle' | 'searching' | 'done' | 'failed'

const STEPS = ['Segment', 'Search', 'Fetch', 'Cut', 'Stitch']

function stepIndex(message: string | null, phase: Phase): number {
  if (phase === 'done') return STEPS.length
  if (!message) return 0
  if (message.includes('Segment')) return 1
  if (message.includes('Search')) return 2
  if (message.includes('Fetch')) return 3
  if (message.includes('Extract')) return 4
  if (message.includes('Concat')) return 5
  return 0
}

function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.ceil(seconds))
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${s % 60}s`
}

export default function HomePage() {
  const [sentence, setSentence] = useState('')
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null)
  const [phase, setPhase] = useState<Phase>('idle')
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [startedAt, setStartedAt] = useState<number | null>(null)
  const [now, setNow] = useState<number>(Date.now())

  const canGenerate = sentence.trim().length >= 2 && turnstileToken !== null && phase !== 'searching'

  useEffect(() => {
    if (phase !== 'searching') return
    setStartedAt((prev) => prev ?? Date.now())
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [phase])

  async function handleGenerate() {
    if (!canGenerate) return
    setPhase('searching')
    setError(null)
    setJob(null)
    setStartedAt(null)
    setNow(Date.now())
    try {
      const { jobId } = await createJob(sentence.trim(), turnstileToken)
      const finished = await waitForJob(jobId, setJob)
      if (finished.status === 'failed') {
        setPhase('failed')
        setError(finished.error ?? 'Generation failed.')
      } else {
        setPhase('done')
      }
    } catch (err) {
      setPhase('failed')
      setError(err instanceof ApiError ? err.message : 'Could not reach the backend.')
    } finally {
      resetTurnstile()
      setTurnstileToken(null)
    }
  }

  const step = stepIndex(job?.message ?? null, phase)

  const elapsed = startedAt ? (now - startedAt) / 1000 : 0
  const frac = step / STEPS.length
  const eta = phase === 'searching' && frac > 0 && elapsed > 0
    ? formatDuration(elapsed / frac - elapsed)
    : null

  return (
    <div className="stage">
      <header className="hero">
        <p className="eyebrow">MOVIE MASHUP ENGINE</p>
        <h1>Type a line.<br />Get a <span className="accent">movie mashup</span>.</h1>
        <p className="subtitle">
          Your sentence is split into phrases by AI, matched against real movie transcripts,
          cut into clips and stitched into one video.
        </p>
      </header>

      <main className="card">
        <textarea
          value={sentence}
          onChange={(e) => setSentence(e.target.value)}
          placeholder={'e.g. "I don\'t know what you\'re talking about. We need to leave right now."'}
          rows={3}
          disabled={phase === 'searching'}
        />

        <div className="row">
          <button className="primary" onClick={handleGenerate} disabled={!canGenerate}>
            {phase === 'searching' ? 'Working…' : 'Generate Video'}
          </button>
          <TurnstileWidget onToken={setTurnstileToken} />
        </div>

        {phase === 'searching' && (
          <div className="pipeline">
            <div className="steps">
              {STEPS.map((label, i) => (
                <div key={label} className={`step ${i < step ? 'done' : i === step ? 'active' : ''}`}>
                  <span className="dot">{i < step ? '✓' : i + 1}</span>
                  <span className="label">{label}</span>
                </div>
              ))}
            </div>
            <p className="status">
              {job?.message ?? 'Starting…'}
              {eta && <span> · ETA {eta}</span>}
            </p>
          </div>
        )}

        {phase === 'failed' && error && <p className="error">Error: {error}</p>}

        {job && job.clips.length > 0 && (
          <section>
            <h2>Selected clips</h2>
            <div className="clips">
              {job.clips.map((clip: Clip, i: number) => (
                <div className="clip-card" key={i}>
                  <span className="clip-num">{i + 1}</span>
                  <div className="clip-body">
                    <p className="quote">“{clip.text}”</p>
                    <p className="meta">
                      {clip.videoTitle} · {clip.character} · {clip.startTime.toFixed(1)}s –{' '}
                      {clip.endTime.toFixed(1)}s
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {phase === 'done' && job && (
          <section className="result">
            <h2>Your video</h2>
            <video controls autoPlay src={outputUrl(job._id)} />
            <p>
              <a className="download" href={outputUrl(job._id)} download>
                ⬇ Download MP4
              </a>
            </p>
          </section>
        )}
      </main>

      <footer>
        <p>
          Clips come from legally usable footage — public-domain films indexed with real
          timestamped transcripts. Source files are fetched on demand and cached.
        </p>
      </footer>
    </div>
  )
}
