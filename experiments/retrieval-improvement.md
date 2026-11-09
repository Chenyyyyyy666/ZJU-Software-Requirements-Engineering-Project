# Raw-turn-window fallback

## Method

The hybrid retriever keeps the first three results from the existing BM25 + MiniLM
RRF ranking. It then uses BM25 over five-turn windows from the original conversation
to fill the remaining two Top-5 slots. Contiguous windows stop at session boundaries,
retain their `(session_id, turn_id)` references, and do not use gold answers or gold
evidence during inference.

This targets a pipeline failure observed in the development errors: an answer's
evidence may exist in the original conversation but be absent from the extracted
MemoryBundle. The fallback returns source-grounded context without replacing the
structured-memory ranking.

## Development-set comparison

The comparison uses only `conv-26`, `conv-30`, `conv-41`, and `conv-42`. Questions
without a non-empty reference answer or gold evidence are excluded from the recall
denominator. Gold evidence is used only after retrieval for scoring.

| Conversation | Questions | Before Recall@1 | After Recall@1 | Before Recall@5 | After Recall@5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `conv-26` | 152 | 0.4145 | 0.4145 | 0.6250 | 0.7961 |
| `conv-30` | 81 | 0.4938 | 0.4938 | 0.6420 | 0.7531 |
| `conv-41` | 152 | 0.5000 | 0.5000 | 0.7566 | 0.7961 |
| `conv-42` | 199 | 0.4673 | 0.4673 | 0.6834 | 0.7739 |
| **All** | **584** | **0.4658** | **0.4658** | **0.6815** | **0.7825** |

Recall@5 increased by `0.1010` (10.10 percentage points), corresponding to 59 more
questions retrieving at least one gold evidence turn. Recall@1 is unchanged.

The independent test split was not used for tuning or measurement. This comparison
does not claim a QA F1 improvement; QA and the project baselines must be rerun under
the same frozen configuration to measure end-to-end answer quality.

## Controlled DeepSeek answer comparison

Both retrieval configurations were then run through the real `deepseek-flash` answer
model on all 586 scored development questions. This is a retrieval+answer ablation:
query analysis and state relation judging are disabled in both runs so the measured
change isolates the retrieval modification. The model, answer prompt (`answer_v1`),
temperature, question set and scoring implementation are identical.

| Conversation | Questions | Before QA F1 | After QA F1 | Delta |
| --- | ---: | ---: | ---: | ---: |
| `conv-26` | 154 | 0.1962 | 0.2479 | +0.0517 |
| `conv-30` | 81 | 0.1951 | 0.2342 | +0.0391 |
| `conv-41` | 152 | 0.2232 | 0.2443 | +0.0211 |
| `conv-42` | 199 | 0.1822 | 0.2171 | +0.0349 |
| **All** | **586** | **0.1983** | **0.2346** | **+0.0363** |

The paired mean F1 improvement is `0.0363` (3.63 percentage points); a fixed-seed
paired bootstrap gives a 95% interval of `[0.0245, 0.0483]`. Of 586 questions, 216
improved, 167 declined and 203 tied. Average response tokens increased from 284.0 to
576.6 per question, while average elapsed time was approximately unchanged at 1.19
seconds. Both runs completed with zero failed questions.

The additional tokens are the expected cost of adding two five-turn raw-context
windows. The independent test split was not measured with this updated algorithm.

## Concise answer prompt

Inspection of the controlled run found that answers averaged about 20 tokens while
gold answers averaged about 5. In 188 cases the normalized gold answer was already a
substring of the generated explanation, but explanatory text reduced token F1.

`answer_v2` therefore asks the model for the shortest exact answer phrase:

- dates, names, values and quantities are returned without a sentence wrapper;
- yes/no questions return only Yes or No;
- explanations are not included in the scored answer;
- conflict metadata remains in the structured `AnswerRecord`.

Using the unchanged improved retrieval results, all 586 development questions were
run again with `deepseek-flash` and zero failures.

| Conversation | Questions | Previous QA F1 | Concise QA F1 | Delta |
| --- | ---: | ---: | ---: | ---: |
| `conv-26` | 154 | 0.2479 | 0.4185 | +0.1706 |
| `conv-30` | 81 | 0.2342 | 0.4292 | +0.1950 |
| `conv-41` | 152 | 0.2443 | 0.4804 | +0.2361 |
| `conv-42` | 199 | 0.2171 | 0.4080 | +0.1909 |
| **All** | **586** | **0.2346** | **0.4325** | **+0.1979** |

The paired improvement is `0.1979` (19.79 percentage points), with a fixed-seed
paired bootstrap 95% interval of `[0.1693, 0.2257]`. Relative to the original
retrieval configuration and `answer_v1`, development QA F1 improved from `0.1983`
to `0.4325` (23.42 percentage points, approximately 2.18x).

The average generated answer length fell from about 20 tokens to 3.6 tokens.
Questions with F1 exactly `1.0` increased from zero to 131. Average response tokens
including the retrieved context were 659.9 and average elapsed time was 1.21 seconds.

The independent test split was not used for tuning or measurement.

## Frozen independent test

After the development comparison, the final retrieval and answer configuration was
frozen before any test question was answered. The freeze record binds the code,
public data, development/test split, all six MemoryBundles, model and parameters:

```text
freeze snapshot: aeef703f6714b53e3ae4837d5dec80d3be415cb71d87aad8f75cf01bb8faeb9c
test run config:  bfb9f9add656ae49d7d1b948ff625a4e63f6b7d32915a03e173cdd6d2ca67ffe
test conversations: conv-43, conv-44
```

The test MemoryBundles were built from raw conversations only with the same
`deepseek-flash`, `memory-facts-v1`, 20-turn/12,000-character chunking and 8,192 output
token configuration as development. No QA answer or gold evidence was used to build
them.

| Conversation | Questions | QA F1 | Recall@1 | Recall@5 |
| --- | ---: | ---: | ---: | ---: |
| `conv-43` | 178 | 0.4783 | 0.4719 | 0.8258 |
| `conv-44` | 123 | 0.4673 | 0.3984 | 0.7480 |
| **All** | **301** | **0.4738** | **0.4419** | **0.7940** |

The run had zero failed questions, used 672.3 tokens per question on average and
1.13 seconds per question. Answers averaged 3.6 tokens and 84 questions had F1 `1.0`.

This test run disables state resolution in both development tuning and test execution
so that it measures the retrieval+answer improvements consistently. It is not a
replacement for a later end-to-end run with state resolution enabled.

## Reproduction

```bash
python -m experiments.measure_retrieval_improvement \
  --data cache/public_locomo10.json \
  --memory-dir outputs/milestone5-b/memory \
  --splits experiments/splits.json \
  --output outputs/eval/retrieval-improvement.json \
  --vector-cache-dir cache/indexes
```
