const statusText = document.getElementById("statusText");
const progressBar = document.getElementById("progressBar");
const audioInput = document.getElementById("audioInput");
const fileName = document.getElementById("fileName");
const generateBtn = document.getElementById("generateBtn");
const log = document.getElementById("log");
const videoPlayer = document.getElementById("videoPlayer");

let projectId = null;
let selectedFile = null;
let pollInterval = null;

const logLine = (message) => {
  const line = document.createElement("div");
  line.textContent = message;
  log.prepend(line);
};

const updateStatus = (data) => {
  statusText.textContent = data.message || "Working";
  progressBar.style.width = `${data.progress || 0}%`;
};

const createProject = async () => {
  const response = await fetch("/api/projects", { method: "POST" });
  const data = await response.json();
  projectId = data.project_id;
  logLine(`Project created: ${projectId}`);
};

const uploadAudio = async () => {
  const form = new FormData();
  form.append("file", selectedFile);
  const response = await fetch(`/api/projects/${projectId}/upload`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    const err = await response.json();
    throw new Error(err.detail || "Upload failed");
  }
  logLine("Audio uploaded.");
};

const startGeneration = async () => {
  const response = await fetch(`/api/projects/${projectId}/generate`, { method: "POST" });
  if (!response.ok) {
    const err = await response.json();
    throw new Error(err.detail || "Failed to start generation");
  }
  logLine("Generation started.");
};

const pollStatus = async () => {
  if (!projectId) return;
  const response = await fetch(`/api/projects/${projectId}/status`);
  if (!response.ok) return;
  const data = await response.json();
  updateStatus(data);

  if (data.state === "done") {
    clearInterval(pollInterval);
    logLine("Video ready! Loading preview.");
    videoPlayer.src = `/api/projects/${projectId}/video`;
  }

  if (data.state === "failed") {
    clearInterval(pollInterval);
    logLine(`Error: ${data.message}`);
  }
};

const setBusy = (busy) => {
  generateBtn.disabled = busy;
  generateBtn.classList.toggle("opacity-50", busy);
};

audioInput.addEventListener("change", (event) => {
  selectedFile = event.target.files[0];
  fileName.textContent = selectedFile ? selectedFile.name : "None";
});

generateBtn.addEventListener("click", async () => {
  try {
    if (!selectedFile) {
      alert("Select an audio file first.");
      return;
    }
    setBusy(true);
    logLine("Booting pipeline...");

    await createProject();
    await uploadAudio();
    await startGeneration();

    pollInterval = setInterval(pollStatus, 2000);
    await pollStatus();
  } catch (err) {
    logLine(`Error: ${err.message}`);
  } finally {
    setBusy(false);
  }
});
