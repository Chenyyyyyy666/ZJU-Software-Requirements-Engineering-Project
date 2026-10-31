const state = { conversationId: "", memories: [], buildId: "", selectedId: "", result: null };
const byId = (id) => document.getElementById(id);

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function toast(message) {
  const element = byId("toast");
  element.textContent = message;
  element.classList.add("show");
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => element.classList.remove("show"), 3200);
}

function renderMemories() {
  const query = byId("memory-filter").value.trim().toLowerCase();
  const items = state.memories.filter((memory) =>
    [memory.content, memory.entity, memory.attribute, memory.value].filter(Boolean).join(" ").toLowerCase().includes(query),
  );
  byId("visible-count").textContent = String(items.length);
  if (!items.length) {
    byId("memory-list").innerHTML = `<div class="empty-state">${state.memories.length ? "没有匹配的记忆。" : "当前对话暂无结构化记忆。"}</div>`;
    return;
  }
  byId("memory-list").innerHTML = items.map((memory) => `
    <button class="memory-item ${memory.memory_id === state.selectedId ? "selected" : ""}" data-memory-id="${esc(memory.memory_id)}">
      <div class="memory-item-top"><span class="memory-entity">${esc(memory.entity || "未识别实体")}</span><span class="memory-date">${esc(memory.event_time || memory.session_time || "时间未知")}</span></div>
      <div class="memory-text">${esc(memory.content)}</div>
      <div class="memory-meta">${esc(memory.attribute || "未分类")} · ${memory.sources.length} 条来源</div>
    </button>`).join("");
  document.querySelectorAll(".memory-item").forEach((button) => button.addEventListener("click", () => showMemory(button.dataset.memoryId)));
}

async function showMemory(memoryId) {
  state.selectedId = memoryId;
  renderMemories();
  byId("evidence-area").innerHTML = `<div class="loading-state">正在读取来源轮次…</div>`;
  try {
    const detail = await request(`/api/memories/${encodeURIComponent(memoryId)}?conversation_id=${encodeURIComponent(state.conversationId)}`);
    if (detail.build_id !== state.buildId) throw new Error("构建版本已变化，请重新选择对话。");
    const memory = detail.memory;
    const turns = detail.source_turns.map((turn) => `
      <div class="source-turn">
        <div class="source-header"><span class="speaker">${esc(turn.speaker)}</span><span class="source-id">S${turn.session_id} · ${esc(turn.turn_id)}</span><span class="source-time">${esc(turn.session_time || "时间未知")}</span></div>
        <div class="source-text">${esc(turn.text)}</div>
      </div>`).join("");
    byId("evidence-area").innerHTML = `
      <div class="detail-memory"><strong>${esc(memory.entity || "记忆详情")} · ${esc(memory.attribute || "未分类")}</strong><p>${esc(memory.content)}</p></div>
      ${turns || `<div class="empty-state">这条记忆没有来源轮次。</div>`}`;
  } catch (error) {
    byId("evidence-area").innerHTML = `<div class="error-banner">${esc(error.message)}</div>`;
  }
}

function renderAnswer(result) {
  const answer = result.answer;
  byId("answer-area").className = "answer-area";
  byId("answer-area").innerHTML = `<span class="answer-orb">✦</span><div><strong>回答</strong><p>${esc(answer.answer)}</p><p class="memory-meta">使用 ${answer.valid_memory_ids.length} 条有效记忆 · ${esc(answer.build_id)}</p></div>`;
  renderRelations(result);
}

function renderRelations(result) {
  const relations = result.relations || [];
  const memoryMap = result.memories || {};
  if (!relations.length) {
    byId("relation-area").innerHTML = `<div class="empty-state">本次候选中没有可比较的状态关系。${result.retrieval.hits.length ? "" : "检索未找到候选记忆。"}</div>`;
    return;
  }
  const validIds = new Set(result.answer.valid_memory_ids);
  const cards = relations.map((relation) => {
    const a = memoryMap[relation.memory_a];
    const b = memoryMap[relation.memory_b];
    if (!a || !b) return "";
    const type = String(relation.relation_type || "UNKNOWN").toLowerCase();
    return `<div class="relation-item">
      <div class="relation-flow">
        <button class="relation-node ${validIds.has(a.memory_id) ? "current" : ""}" data-memory-id="${esc(a.memory_id)}"><small>${validIds.has(a.memory_id) ? "当前答案依据" : "记忆 A"}</small><span>${esc(a.content)}</span></button>
        <div class="relation-badge ${esc(type)}">${esc(relation.relation_type)}<br/>关系</div>
        <button class="relation-node ${validIds.has(b.memory_id) ? "current" : ""}" data-memory-id="${esc(b.memory_id)}"><small>${validIds.has(b.memory_id) ? "当前答案依据" : "记忆 B"}</small><span>${esc(b.content)}</span></button>
      </div><div class="relation-reason">${esc(relation.reason)}</div>
    </div>`;
  }).join("");
  byId("relation-area").innerHTML = `${cards}<div class="relation-legend"><span class="legend-chip">RETAIN 保留</span><span class="legend-chip">UPDATE 更新</span><span class="legend-chip">SUPERSEDE 替代</span><span class="legend-chip">CONFLICT 冲突</span></div>`;
  document.querySelectorAll(".relation-node").forEach((button) => button.addEventListener("click", () => showMemory(button.dataset.memoryId)));
}

async function loadConversationList() {
  const data = await request("/api/conversations");
  const select = byId("conversation-select");
  if (!data.conversations.length) {
    select.innerHTML = `<option value="">暂无记忆数据</option>`;
    byId("memory-list").innerHTML = `<div class="empty-state">未找到 MemoryBundle。请先运行记忆构建流程；默认目录为 <code>outputs/memory/</code>。</div>`;
    byId("evidence-area").innerHTML = `<div class="empty-state">构建记忆后，可在此回看证据。</div>`;
    byId("memory-count").textContent = "0";
    byId("visible-count").textContent = "0";
    byId("build-id").textContent = "无构建数据";
    return;
  }
  select.innerHTML = data.conversations.map((item) => `<option value="${esc(item.conversation_id)}">${esc(item.conversation_id)}</option>`).join("");
  await selectConversation(data.conversations[0].conversation_id);
  select.addEventListener("change", () => selectConversation(select.value));
}

async function selectConversation(conversationId) {
  if (!conversationId) return;
  state.conversationId = conversationId;
  state.result = null;
  byId("answer-area").className = "answer-area empty-answer";
  byId("answer-area").innerHTML = `<span class="answer-orb">✦</span><div><strong>答案会显示在这里</strong><p>提交问题后，查看答案依据和记忆间的状态关系。</p></div>`;
  byId("relation-area").innerHTML = `<div class="empty-state">提出问题后，关系解析结果会出现在这里。</div>`;
  byId("evidence-area").innerHTML = `<div class="empty-state">从左侧选择一条记忆，查看它对应的原始对话轮次。</div>`;
  try {
    const data = await request(`/api/memories?conversation_id=${encodeURIComponent(conversationId)}`);
    state.memories = data.memories;
    state.buildId = data.build_id;
    state.selectedId = "";
    byId("memory-count").textContent = String(data.memories.length);
    byId("build-id").textContent = data.build_id;
    renderMemories();
  } catch (error) {
    toast(error.message);
  }
}

byId("memory-filter").addEventListener("input", renderMemories);
byId("question-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = byId("question-input").value.trim();
  if (!state.conversationId) return toast("请先选择一段对话。");
  if (!question) return toast("请输入问题。");
  const button = byId("ask-button");
  button.disabled = true;
  button.textContent = "正在分析…";
  byId("relation-area").innerHTML = `<div class="loading-state">检索记忆并分析状态关系…</div>`;
  try {
    const result = await request("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: state.conversationId, question }),
    });
    if (result.build_id !== state.buildId) throw new Error("问答结果对应其他构建版本，请重新提交问题。");
    state.result = result;
    renderAnswer(result);
  } catch (error) {
    byId("answer-area").className = "error-banner";
    byId("answer-area").textContent = error.message;
    byId("relation-area").innerHTML = `<div class="empty-state">本次问答未能完成状态解析。</div>`;
  } finally {
    button.disabled = false;
    button.innerHTML = "<span>✦</span>分析并回答";
  }
});

loadConversationList().catch((error) => {
  byId("conversation-select").innerHTML = `<option value="">服务连接失败</option>`;
  byId("memory-list").innerHTML = `<div class="error-banner">${esc(error.message)}</div>`;
});
