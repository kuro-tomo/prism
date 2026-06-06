/**
 * useSTT — Web Speech API 音声入力フック
 * index.html の STT 実装を React フックに移植
 * Chrome / Edge のみ対応（Web Speech API のプレフィックス含む）
 */
"use client"

import { useCallback, useEffect, useRef, useState } from "react"

// SpeechRecognition の型宣言
declare global {
  interface Window {
    SpeechRecognition?: new () => SpeechRecognition
    webkitSpeechRecognition?: new () => SpeechRecognition
  }
}

interface SpeechRecognition extends EventTarget {
  lang: string
  interimResults: boolean
  maxAlternatives: number
  continuous: boolean
  start(): void
  stop(): void
  abort(): void
  onresult: ((event: SpeechRecognitionEvent) => void) | null
  onerror: ((event: SpeechRecognitionErrorEvent) => void) | null
  onend: (() => void) | null
}

interface SpeechRecognitionEvent extends Event {
  resultIndex: number
  results: SpeechRecognitionResultList
}

interface SpeechRecognitionResultList {
  length: number
  item(index: number): SpeechRecognitionResult
  [index: number]: SpeechRecognitionResult
}

interface SpeechRecognitionResult {
  isFinal: boolean
  [index: number]: SpeechRecognitionAlternative
}

interface SpeechRecognitionAlternative {
  transcript: string
  confidence: number
}

interface SpeechRecognitionErrorEvent extends Event {
  error: string
  message: string
}

export type SttStatus = "idle" | "listening" | "error"

export function useSTT(onTranscript: (text: string, isFinal: boolean) => void) {
  const [status, setStatus] = useState<SttStatus>("idle")
  const [supported, setSupported] = useState(false)
  const recRef = useRef<SpeechRecognition | null>(null)

  useEffect(() => {
    if (typeof window === "undefined") return
    const SR = window.SpeechRecognition ?? window.webkitSpeechRecognition
    if (!SR) return
    setSupported(true)

    const rec = new SR()
    rec.lang = "ja-JP"
    rec.interimResults = true
    rec.maxAlternatives = 1
    rec.continuous = false
    recRef.current = rec
  }, [])

  const start = useCallback(() => {
    if (!recRef.current || status === "listening") return
    const rec = recRef.current

    rec.onresult = (e: SpeechRecognitionEvent) => {
      let interim = ""
      let final = ""
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript
        if (e.results[i].isFinal) final += t
        else interim += t
      }
      if (final) {
        onTranscript(final, true)
      } else if (interim) {
        onTranscript(interim, false)
      }
    }

    rec.onerror = (e: SpeechRecognitionErrorEvent) => {
      if (e.error !== "aborted") setStatus("error")
    }

    rec.onend = () => {
      setStatus("idle")
    }

    rec.start()
    setStatus("listening")
  }, [status, onTranscript])

  const stop = useCallback(() => {
    recRef.current?.stop()
    setStatus("idle")
  }, [])

  return { status, supported, start, stop }
}
