/**
 * useTTS — Web Speech API 音声読み上げフック
 * キュードレイン方式（並列 agent_done の上書き防止）
 * deliberation.html の TTS 実装を React フックに移植
 */
"use client"

import { useCallback, useEffect, useRef, useState } from "react"

interface TtsItem {
  text: string
  rate: number
}

export function useTTS() {
  const [voiceOn, setVoiceOn] = useState(false)
  const [supported, setSupported] = useState(false)

  const queueRef    = useRef<TtsItem[]>([])
  const speakingRef = useRef(false)
  const voiceRef    = useRef<SpeechSynthesisVoice | null>(null)

  // 音声一覧ロード（非同期）
  useEffect(() => {
    if (typeof window === "undefined" || !window.speechSynthesis) return
    setSupported(true)

    const loadVoice = () => {
      const voices = window.speechSynthesis.getVoices()
      voiceRef.current = voices.find((v) => v.lang.startsWith("ja")) ?? null
    }
    loadVoice()
    window.speechSynthesis.onvoiceschanged = loadVoice

    return () => {
      if (window.speechSynthesis) window.speechSynthesis.onvoiceschanged = null
    }
  }, [])

  // キュードレイン
  const drain = useCallback(() => {
    if (!voiceOn || !window.speechSynthesis || queueRef.current.length === 0) {
      speakingRef.current = false
      return
    }
    speakingRef.current = true
    const item = queueRef.current.shift()!
    const utt = new SpeechSynthesisUtterance(item.text)
    utt.lang = "ja-JP"
    utt.rate = item.rate
    if (voiceRef.current) utt.voice = voiceRef.current
    utt.onend = drain
    window.speechSynthesis.speak(utt)
  }, [voiceOn])

  // スピーク（エンキュー）
  const speak = useCallback(
    (text: string, rate = 1.05) => {
      if (!voiceOn || !window.speechSynthesis || !text) return
      queueRef.current.push({ text, rate })
      if (!speakingRef.current) drain()
    },
    [voiceOn, drain],
  )

  // トグル
  const toggle = useCallback(() => {
    setVoiceOn((prev) => {
      if (prev) {
        // OFF にする
        queueRef.current = []
        speakingRef.current = false
        if (window.speechSynthesis) window.speechSynthesis.cancel()
      }
      return !prev
    })
  }, [])

  return { voiceOn, supported, speak, toggle }
}
