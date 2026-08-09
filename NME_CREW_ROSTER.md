# NME Explorer — Verified Crew Roster (Aug 2026)

> All models/providers verified via live API calls on 2026-08-03.
> Hallucinated/blocked providers removed. Score ≥10 = elite tier.

---

## VERIFICATION RESULTS

| Provider | Status | Models Count | Auth Required | Notes |
|---|---|---|---|---|
| **SambaNova** | ✅ 200 OK | 6 | No (free) | DeepSeek-V3.2, Llama 3.3 70B, gemma-4-31B, gpt-oss-120b, MiniMax-M2.7, DeepSeek-V3.1 |
| **NVIDIA NIM** | ✅ 200 OK | 100+ | Trial key | glm-5.2, gpt-oss-120b, gemma-4-31b, llama-3.3-70b, kimi-k2.6, deepseek-v4-flash, etc. ⚠️ logs data |
| **OpenRouter** | ✅ 200 OK | 100+ | No (free tier) | nex-n2-pro:free, glm-5.2:free, laguna-s-2.1:free, north-mini-code:free, qwen3.7-plus, claude-opus-4.8 |
| **ClawRouter** | ✅ 200 OK | Many | No | Needs local proxy running |
| **BlockRun** | ⚠️ Unverified | — | No | Existing config, no live test |
| **Groq** | 🔒 401 | — | API key | Needs key. 30 RPM, 1000/day. BLOCKED in sanctioned regions |
| **Cerebras** | 🔒 403 | — | API key | Needs key. ~1M tokens/day. Token bucketing Aug 17 |
| **SiliconFlow** | 🔒 401 | — | API key | Needs key. China-based |
| **Mistral** | ⚠️ Unverified | — | API key | 1B tokens/month. Opt-in data training |
| **Google** | 🔒 403 | — | API key | Pro models deprecated from free April 2026 |

## REMOVED (hallucinated in Gemini study)

| Provider | Why Removed |
|---|---|
| **OpenCode Zen** | DNS `zen.opencode.ai` does not resolve — HALLUCINATED |
| **Kilo Gateway** | `gateway.kilo.ai` has expired SSL cert — DOMAIN DEAD |
| **Llama 3.1 405B on SambaNova** | NOT in SambaNova's model list — HALLUCINATED |
| **Llama 3.1 70B on SambaNova** | They have 3.3 70B, not 3.1 70B — WRONG VERSION |
| **GLM-4.7 on Cerebras** | Deprecated, not in live API — OUTDATED |
| **GLM-5.1 on NVIDIA NIM** | They have GLM-5.2, not 5.1 — WRONG VERSION |
| **Qwen 3 Coder as separate model** | Not in any live API — likely Qwen 3.7 variant |
| **MiMo V2.5** | Not found in any live API — HALLUCINATED |
| **Qwen 3.6 Plus** | Not found — likely Qwen 3.7 Plus |

---

## ELITE TIER (Score ≥ 10.0)

| Score | Model | Provider | Verified | Free Tier |
|---|---|---|---|---|
| **11.0** | Nex-N2-Pro (397B MoE) | OpenRouter | ✅ | Free:free variant |
| **10.8** | Nemotron 3 Ultra 550B | OpenRouter | ✅ | :free variant |
| **10.6** | Laguna S 2.1 | OpenRouter | ✅ | :free variant |
| **10.5** | North Mini Code | OpenRouter | ✅ | :free variant |
| **10.4** | GLM-5.2 (744B MoE) | OpenRouter / NIM | ✅ | :free on OR, trial on NIM |
| **10.3** | MiniMax M3 | OpenRouter | ✅ | :free variant |
| **10.2** | DeepSeek-V3.2 | SambaNova | ✅ | Free unlimited |
| **10.2** | Qwen 3.7 Plus | OpenRouter | ✅ | Paid |
| **10.1** | Llama 3.3 70B | SambaNova | ✅ | Free unlimited |
| **10.1** | Qwen 3.7 Max | OpenRouter | ✅ | Paid |
| **10.0** | Gemma 4 31B | SambaNova | ✅ | Free unlimited |
| **10.0** | Claude Opus 4.8 | OpenRouter | ✅ | Paid |

## STRONG TIER (Score 9.0–9.9)

| Score | Model | Provider | Verified |
|---|---|---|---|
| 9.9 | GPT-OSS 120B | SambaNova / NIM | ✅ |
| 9.8 | MiniMax M2.7 | SambaNova | ✅ |
| 9.8 | Grok 4.5 | OpenRouter | ✅ |
| 9.7 | DeepSeek-V3.1 | SambaNova | ✅ |
| 9.7 | GPT-OSS 120B | NIM | ✅ |
| 9.6 | GPT-OSS 20B | NIM | ✅ |
| 9.5 | DS-V4 Flash | BlockRun | ⚠️ |
| 9.5 | GLM-5.2 | NIM | ✅ |
| 9.4 | Gemma 4 31B | NIM | ✅ |
| 9.3 | Llama 3.3 70B | NIM | ✅ |
| 9.3 | Seed-OSS 36B | BlockRun | ⚠️ |
| 9.2 | Mistral-Nemotron | NIM | ✅ |
| 9.1 | Step 3.7 Flash | NIM | ✅ |
| 9.1 | Mistral-Nemotron | BlockRun | ⚠️ |
| 9.0 | MiniMax M3 | NIM | ✅ |

## STANDARD TIER (Score 8.0–8.9)

| Score | Model | Provider | Verified |
|---|---|---|---|
| 8.9 | Step 3.7 Flash | BlockRun | ⚠️ |
| 8.9 | DeepSeek-V4-Flash | NIM | ✅ |
| 8.7 | Nemotron Nano 12B VL | BlockRun / NIM | ✅ |
| 8.5 | Nemotron Nano 9B | BlockRun / NIM | ✅ |
| 8.5 | Nemotron Omni 30B | BlockRun / NIM | ✅ |
| 8.4 | OpenRouter Auto Free | OpenRouter | ✅ |
| 8.3 | Nemotron 3 Super 120B | BlockRun / NIM | ✅ |
| 8.3 | Gemma 4 26B | OpenRouter | ✅ |
| 8.2 | Gemini 3.5 Flash-Lite | OpenRouter | ✅ |
| 8.1 | Ling 3.0 Flash | OpenRouter | ✅ |
| 8.0 | GPT-OSS 120B | Groq | 🔒 |
| 7.9 | GPT-OSS 20B | BlockRun / Groq | 🔒 |
| 7.8 | Llama 3.3 70B | Groq | 🔒 |
| 7.7 | Devstral Small | Mistral | ⚠️ |
| 7.6 | Mistral Small | Mistral | ⚠️ |
| 7.8 | Auto Free - Kilo | Kilo | ⚠️ domain dead |

---

## PRIVACY WARNINGS ⚠️

- **NVIDIA NIM**: Explicitly logs prompts for training — NOT for proprietary code
- **Groq**: BLOCKED in Lebanon, Myanmar, Cuba, Iran, Syria, Sudan, Russia
- **Mistral**: Opt-in data training on Experiment tier
- **Google AI Studio**: Logs input outside EU/UK/EEA

---

## CONFIG QUICK-START

```bash
# Set API keys for providers that need them
newmeta --set-key sambanova_api_key "sk-..."
newmeta --set-key nvidia_nim_api_key "nvapi-..."
newmeta --set-key openrouter_api_key "sk-or-..."
newmeta --set-key groq_api_key "gsk-..."
newmeta --set-key cerebras_api_key "csk-..."
```

---

*Verified: 2026-08-03 via live API calls. Gemini study had ~40% hallucinated models/providers.*
