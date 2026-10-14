const form = document.querySelector("#chat-form");
const input = document.querySelector("#question");
const conversation = document.querySelector("#conversation");
const sendButton = form.querySelector("button[type='submit']");
const welcome = document.querySelector("#welcome");
const uploadInput = document.querySelector("#pdf-upload");
const uploadButton = document.querySelector("#upload-trigger");
const uploadStatus = document.querySelector("#upload-status");
const documentList = document.querySelector("#document-list");
const libraryCount = document.querySelector("#library-count");
const csrfToken = document.querySelector("meta[name='csrf-token']")?.content || "";
let chatInProgress = false;
let uploadInProgress = false;

function addMessage(role, text, sources = []) {
  const message = document.createElement("article");
  message.className = `message ${role}`;

  const label = document.createElement("div");
  label.className = "message-label";
  label.textContent = role === "user" ? "YOU" : "REFERENCE ASSISTANT";

  const body = document.createElement("div");
  body.className = "message-body";
  body.textContent = text;
  message.append(label, body);

  if (sources.length) {
    const sourceLine = document.createElement("p");
    sourceLine.className = "source-list";
    const heading = document.createElement("strong");
    heading.textContent = "Sources: ";
    sourceLine.append(heading, document.createTextNode(sources.join(" · ")));
    message.append(sourceLine);
  }

  conversation.append(message);
  message.scrollIntoView({ behavior: "smooth", block: "nearest" });
  return message;
}

async function submitQuestion(question) {
  const normalizedQuestion = question.trim();
  if (!normalizedQuestion || chatInProgress || uploadInProgress) return;
  welcome.hidden = true;
  addMessage("user", normalizedQuestion);
  input.value = "";
  input.style.height = "auto";
  chatInProgress = true;
  sendButton.disabled = true;
  const loading = addMessage("assistant", "Looking through the reference library...");
  loading.querySelector(".message-body").classList.add("typing");

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
      body: JSON.stringify({ question: normalizedQuestion }),
    });
    const result = await response.json();
    loading.remove();
    if (!response.ok) throw new Error(result.error || "The request could not be completed.");
    addMessage("assistant", result.answer, result.sources || []);
  } catch (error) {
    loading.remove();
    addMessage("assistant", error.message || "The assistant is temporarily unavailable. Please try again.");
  } finally {
    chatInProgress = false;
    sendButton.disabled = uploadInProgress;
    input.focus();
  }
}

function renderDocuments(documents) {
  documentList.replaceChildren();
  libraryCount.textContent = `${documents.length} ${documents.length === 1 ? "document" : "documents"}`;
  if (!documents.length) {
    const empty = document.createElement("span");
    empty.className = "document-empty";
    empty.textContent = "No PDFs added yet";
    documentList.append(empty);
    return;
  }

  for (const documentRecord of documents) {
    const item = document.createElement("div");
    item.className = "document-item";
    const name = document.createElement("span");
    name.className = "document-name";
    name.textContent = documentRecord.filename;
    name.title = documentRecord.filename;
    const expiry = document.createElement("span");
    expiry.className = "document-expiry";
    expiry.textContent = `expires ${new Date(documentRecord.expires_at).toLocaleDateString()}`;
    const remove = document.createElement("button");
    remove.className = "document-delete";
    remove.type = "button";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Delete ${documentRecord.filename}`);
    remove.title = "Delete this document";
    remove.addEventListener("click", () => deleteDocument(documentRecord, remove));
    item.append(name, expiry, remove);
    documentList.append(item);
  }
}

async function loadDocuments() {
  try {
    const response = await fetch("/api/documents");
    if (!response.ok) throw new Error("Could not load your documents.");
    const result = await response.json();
    renderDocuments(result.documents || []);
  } catch (_error) {
    libraryCount.textContent = "Unavailable";
    documentList.replaceChildren();
  }
}

async function deleteDocument(documentRecord, button) {
  if (!window.confirm(`Delete ${documentRecord.filename} from your library?`)) return;
  button.disabled = true;
  try {
    const response = await fetch(`/api/documents/${encodeURIComponent(documentRecord.document_id)}`, {
      method: "DELETE",
      headers: { "X-CSRFToken": csrfToken },
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not delete this document.");
    uploadStatus.textContent = result.message;
    uploadStatus.className = "upload-status success";
    await loadDocuments();
  } catch (error) {
    uploadStatus.textContent = error.message || "Could not delete this document.";
    uploadStatus.className = "upload-status error";
    button.disabled = false;
  }
}

async function uploadReference(file) {
  if (chatInProgress || uploadInProgress) return;
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    uploadStatus.textContent = "Choose a PDF file.";
    uploadStatus.className = "upload-status error";
    return;
  }
  if (file.size > 15 * 1024 * 1024) {
    uploadStatus.textContent = "PDF files must be 15 MB or smaller.";
    uploadStatus.className = "upload-status error";
    return;
  }

  const formData = new FormData();
  formData.append("file", file);
  uploadInProgress = true;
  uploadButton.disabled = true;
  sendButton.disabled = true;
  uploadStatus.textContent = `Uploading and indexing ${file.name}...`;
  uploadStatus.className = "upload-status";

  try {
    const response = await fetch("/api/documents", {
      method: "POST",
      headers: { "X-CSRFToken": csrfToken },
      body: formData,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "The PDF could not be indexed.");
    uploadStatus.textContent = result.message;
    uploadStatus.className = "upload-status success";
    await loadDocuments();
  } catch (error) {
    uploadStatus.textContent = error.message || "The PDF could not be indexed. Please try again.";
    uploadStatus.className = "upload-status error";
  } finally {
    uploadInProgress = false;
    uploadButton.disabled = false;
    sendButton.disabled = chatInProgress;
    uploadInput.value = "";
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  submitQuestion(input.value);
});

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

document.querySelectorAll("[data-question]").forEach((button) => {
  button.addEventListener("click", () => submitQuestion(button.dataset.question));
});

uploadButton.addEventListener("click", () => uploadInput.click());
uploadInput.addEventListener("change", () => {
  const [file] = uploadInput.files;
  if (file) uploadReference(file);
});

loadDocuments();