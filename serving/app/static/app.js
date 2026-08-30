(() => {
  "use strict";

  const THREAD_ID_KEY = "kubemind.thread-id";
  const SAFE_ERROR_MESSAGE = "Something went wrong while processing that question. Please try again.";
  const form = document.querySelector("#composer");
  const textarea = document.querySelector("#question");
  const sendButton = document.querySelector("#send");
  const welcome = document.querySelector("#welcome");
  const conversation = document.querySelector("#conversation");
  const messages = document.querySelector("#messages");
  const suggestions = document.querySelectorAll(".suggestion");
  let isSending = false;

  function newThreadId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    return `thread-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function threadId() {
    let id = window.sessionStorage.getItem(THREAD_ID_KEY);
    if (!id) {
      id = newThreadId();
      window.sessionStorage.setItem(THREAD_ID_KEY, id);
    }
    return id;
  }

  function resizeComposer() {
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 160)}px`;
  }

  function beginConversation() {
    welcome.hidden = true;
    conversation.hidden = false;
  }

  function appendInlineText(container, value) {
    const tokens = value.split(/(\[[^\]]+\]\(https?:\/\/[^)\s]+\)|\[\d+\]|`[^`]+`|\*\*[^*]+\*\*)/g);
    for (const token of tokens) {
      if (!token) continue;
      const markdownLink = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/.exec(token);
      if (markdownLink) {
        try {
          const url = new URL(markdownLink[2]);
          if (url.protocol === "https:" || url.protocol === "http:") {
            const link = document.createElement("a");
            link.href = url.href;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.textContent = markdownLink[1];
            container.append(link);
            continue;
          }
        } catch {
          // Treat malformed links as normal text below.
        }
      }
      if (token.startsWith("`") && token.endsWith("`")) {
        const code = document.createElement("code");
        code.textContent = token.slice(1, -1);
        container.append(code);
      } else if (/^\[\d+\]$/.test(token)) {
        const citation = document.createElement("span");
        citation.className = "citation";
        citation.textContent = token;
        container.append(citation);
      } else if (token.startsWith("**") && token.endsWith("**")) {
        const strong = document.createElement("strong");
        strong.textContent = token.slice(2, -2);
        container.append(strong);
      } else {
        container.append(document.createTextNode(token));
      }
    }
  }

  function appendParagraph(container, lines) {
    const paragraph = document.createElement("p");
    lines.forEach((line, index) => {
      if (index > 0) paragraph.append(document.createElement("br"));
      appendInlineText(paragraph, line);
    });
    container.append(paragraph);
  }

  function appendList(container, lines, ordered) {
    const list = document.createElement(ordered ? "ol" : "ul");
    const marker = ordered ? /^\s*\d+[.)]\s+/ : /^\s*[-*+]\s+/;
    for (const line of lines) {
      const item = document.createElement("li");
      appendInlineText(item, line.replace(marker, ""));
      list.append(item);
    }
    container.append(list);
  }

  function renderAnswer(container, answer) {
    const lines = answer.replace(/\r\n?/g, "\n").split("\n");
    let index = 0;

    while (index < lines.length) {
      if (!lines[index].trim()) {
        index += 1;
        continue;
      }

      if (lines[index].trim().startsWith("```")) {
        const language = lines[index].trim().slice(3).trim();
        const codeLines = [];
        index += 1;
        while (index < lines.length && !lines[index].trim().startsWith("```")) {
          codeLines.push(lines[index]);
          index += 1;
        }
        if (index < lines.length) index += 1;
        const pre = document.createElement("pre");
        const code = document.createElement("code");
        if (language) code.dataset.language = language;
        code.textContent = codeLines.join("\n");
        pre.append(code);
        container.append(pre);
        continue;
      }

      const heading = /^(#{1,6})\s+(.+?)\s*#*$/.exec(lines[index].trim());
      if (heading) {
        const title = document.createElement("p");
        title.className = "message-heading";
        appendInlineText(title, heading[2]);
        container.append(title);
        index += 1;
        continue;
      }

      const isUnorderedList = /^\s*[-*+]\s+/.test(lines[index]);
      const isOrderedList = /^\s*\d+[.)]\s+/.test(lines[index]);
      if (isUnorderedList || isOrderedList) {
        const pattern = isOrderedList ? /^\s*\d+[.)]\s+/ : /^\s*[-*+]\s+/;
        const listLines = [];
        while (index < lines.length && pattern.test(lines[index])) {
          listLines.push(lines[index]);
          index += 1;
        }
        appendList(container, listLines, isOrderedList);
        continue;
      }

      const paragraphLines = [];
      while (
        index < lines.length &&
        lines[index].trim() &&
        !lines[index].trim().startsWith("```") &&
        !/^\s*[-*+]\s+/.test(lines[index]) &&
        !/^\s*\d+[.)]\s+/.test(lines[index])
      ) {
        paragraphLines.push(lines[index]);
        index += 1;
      }
      appendParagraph(container, paragraphLines);
    }
  }

  function renderSources(container, sources) {
    if (!Array.isArray(sources) || !sources.length) return;
    const section = document.createElement("section");
    section.className = "message-sources";
    const heading = document.createElement("p");
    heading.className = "message-sources-title";
    heading.textContent = "Sources";
    const list = document.createElement("ul");

    for (const source of sources) {
      if (!source || typeof source !== "object" || typeof source.id !== "string" || typeof source.title !== "string") {
        continue;
      }
      const item = document.createElement("li");
      item.className = "source-item";
      const citation = document.createElement("span");
      citation.className = "source-id";
      citation.textContent = `[${source.id}]`;
      const details = document.createElement("span");
      details.className = "source-details";
      const label = source.title;
      if (typeof source.url === "string" && source.url) {
        try {
          const url = new URL(source.url);
          if (url.protocol === "https:" || url.protocol === "http:") {
            const link = document.createElement("a");
            link.href = url.href;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.textContent = label;
            details.append(link);
          } else {
            details.append(document.createTextNode(label));
          }
        } catch {
          details.append(document.createTextNode(label));
        }
      } else {
        details.append(document.createTextNode(label));
      }
      const metadata = [source.section, source.source].filter((value) => typeof value === "string" && value);
      if (metadata.length) {
        const meta = document.createElement("span");
        meta.className = "source-meta";
        meta.textContent = metadata.join(" · ");
        details.append(meta);
      }
      item.append(citation, details);
      list.append(item);
    }
    if (list.childElementCount) {
      section.append(heading, list);
      container.append(section);
    }
  }

  function createMessage(role, text, sources = []) {
    const article = document.createElement("article");
    article.className = `message message--${role}`;
    const label = document.createElement("p");
    label.className = "message-label";
    label.textContent = role === "user" ? "You" : "KubeMind";
    const content = document.createElement("div");
    content.className = "message-content";

    if (role === "assistant") {
      renderAnswer(content, text);
      renderSources(content, sources);
    } else {
      content.textContent = text;
    }
    article.append(label, content);
    return article;
  }

  function createThinkingMessage() {
    const article = document.createElement("article");
    article.className = "message message--assistant message--thinking";
    article.setAttribute("aria-label", "KubeMind is thinking");
    const label = document.createElement("p");
    label.className = "message-label";
    label.textContent = "KubeMind";
    const content = document.createElement("div");
    content.className = "message-content thinking";
    for (let index = 0; index < 3; index += 1) {
      content.append(document.createElement("span"));
    }
    const status = document.createElement("span");
    status.className = "sr-only";
    status.textContent = "Thinking";
    content.append(status);
    article.append(label, content);
    return article;
  }

  function scrollToLatest() {
    window.requestAnimationFrame(() => {
      window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
    });
  }

  function setSending(sending) {
    isSending = sending;
    textarea.disabled = sending;
    sendButton.disabled = sending;
    form.setAttribute("aria-busy", String(sending));
  }

  function updateAssistantMessage(message, answer, sources = []) {
    const content = message.querySelector(".message-content");
    content.replaceChildren();
    renderAnswer(content, answer);
    renderSources(content, sources);
    scrollToLatest();
  }

  function parseSseEvent(block) {
    let event = "message";
    const data = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (!data.length) return null;
    return { event, payload: JSON.parse(data.join("\n")) };
  }

  async function streamAnswer(question, handleEvent) {
    const response = await window.fetch("/ask/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, thread_id: threadId() }),
    });
    if (!response.ok || !response.body) throw new Error("The assistant request failed");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let completed = false;

    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const rawEvent = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const event = parseSseEvent(rawEvent);
        if (event) {
          if (event.event === "error") throw new Error(event.payload.message || "The assistant request failed");
          handleEvent(event);
          if (event.event === "done") completed = true;
        }
        boundary = buffer.indexOf("\n\n");
      }
      if (done) break;
    }

    if (!completed) throw new Error("The assistant stream ended unexpectedly");
  }

  async function submitQuestion(question) {
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion || isSending) return;

    beginConversation();
    messages.append(createMessage("user", trimmedQuestion));
    const thinking = createThinkingMessage();
    messages.append(thinking);
    textarea.value = "";
    resizeComposer();
    setSending(true);
    scrollToLatest();
    let assistantMessage = null;
    let streamedAnswer = "";
    let sources = [];

    function showAssistant(answer) {
      if (!assistantMessage) {
        assistantMessage = createMessage("assistant", answer, sources);
        thinking.replaceWith(assistantMessage);
      } else {
        updateAssistantMessage(assistantMessage, answer, sources);
      }
      scrollToLatest();
    }

    try {
      await streamAnswer(trimmedQuestion, ({ event, payload }) => {
        if (event === "delta" && typeof payload.text === "string") {
          streamedAnswer += payload.text;
          showAssistant(streamedAnswer);
        } else if (event === "replace" && typeof payload.answer === "string") {
          streamedAnswer = payload.answer;
          showAssistant(streamedAnswer);
        } else if (event === "sources" && Array.isArray(payload.sources)) {
          sources = payload.sources;
          if (assistantMessage) showAssistant(streamedAnswer);
        }
      });
      if (!assistantMessage) throw new Error("The assistant stream returned no answer");
    } catch (error) {
      console.error("KubeMind request failed", error);
      if (assistantMessage) {
        updateAssistantMessage(assistantMessage, SAFE_ERROR_MESSAGE);
      } else {
        thinking.replaceWith(createMessage("assistant", SAFE_ERROR_MESSAGE));
      }
    } finally {
      setSending(false);
      textarea.focus();
      scrollToLatest();
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submitQuestion(textarea.value);
  });

  textarea.addEventListener("input", resizeComposer);
  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  for (const suggestion of suggestions) {
    suggestion.addEventListener("click", () => submitQuestion(suggestion.dataset.question || ""));
  }

  threadId();
  resizeComposer();
})();
