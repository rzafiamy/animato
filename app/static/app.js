const statusText = document.getElementById("statusText");
const progressBar = document.getElementById("progressBar");
const progressPercent = document.getElementById("progressPercent");
const statusBadge = document.getElementById("statusBadge");
const audioInput = document.getElementById("audioInput");
const fileName = document.getElementById("fileName");
const generateBtn = document.getElementById("generateBtn");
const log = document.getElementById("log");
const videoPlayer = document.getElementById("videoPlayer");
const videoPlaceholder = document.getElementById("videoPlaceholder");
const videoControls = document.getElementById("videoControls");
const dropZone = document.getElementById("dropZone");

const asrStep = document.getElementById("asrStep");
const renderStep = document.getElementById("renderStep");
const doneStep = document.getElementById("doneStep");
const errorAlert = document.getElementById("errorAlert");
const errorMessage = document.getElementById("errorMessage");

let projectId = null;
let selectedFile = null;
let pollInterval = null;

const logLine = (message, type = "info") => {
  const line = document.createElement("div");
  line.className = "flex items-center gap-2 py-0.5 animate-in fade-in slide-in-from-left-2 duration-300";
  
  let icon = "chevron-right";
  let colorClass = "text-slate-600";
  
  if (type === "success") {
    icon = "check-circle-2";
    colorClass = "text-emerald-500";
  } else if (type === "error") {
    icon = "alert-circle";
    colorClass = "text-rose-500";
  } else if (type === "system") {
    icon = "cpu";
    colorClass = "text-sky-500";
  }

  line.innerHTML = `
    <i data-lucide="${icon}" class="w-3 h-3 ${colorClass}"></i>
    <span class="${type === 'error' ? 'text-rose-400 font-bold' : type === 'system' ? 'text-sky-400 font-bold' : 'text-slate-400'}">${message}</span>
  `;
  
  log.prepend(line);
  lucide.createIcons();
};

const updateUIState = (data) => {
  statusText.textContent = data.message || "Working";
  const progress = data.progress || 0;
  progressBar.style.width = `${progress}%`;
  progressPercent.textContent = `${progress}%`;

  if (data.state === "asr") {
    asrStep.classList.remove("opacity-20");
    asrStep.classList.add("opacity-100");
  } else if (data.state === "render") {
    asrStep.classList.add("opacity-100");
    renderStep.classList.remove("opacity-20");
    renderStep.classList.add("opacity-100");
  } else if (data.state === "done") {
    asrStep.classList.add("opacity-100");
    renderStep.classList.add("opacity-100");
    doneStep.classList.remove("opacity-20");
    doneStep.classList.add("opacity-100");

    statusBadge.textContent = "COMPLETED";
    statusBadge.classList.replace("text-slate-500", "text-emerald-400");
    statusBadge.classList.add("bg-emerald-500/10", "border-emerald-500/20");
  }
};

const createProject = async () => {
  logLine("Initializing neural workspace...", "system");
  const response = await fetch("/api/projects", { method: "POST" });
  const data = await response.json();
  projectId = data.project_id;
  document.getElementById("currentProjectLabel").textContent = `ID: ${projectId.substring(0,8)}`;
  logLine(`Workspace locked: ${projectId}`, "success");
};

const uploadAudio = async () => {
  logLine("Streaming audio buffers to server...", "system");
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
  logLine("Audio synchronization complete.", "success");
};

const forceReset = document.getElementById("forceReset");

const startGeneration = async () => {
  logLine("Starting neural render engine...", "system");
  if (errorAlert) errorAlert.classList.add("hidden");
  
  const isReset = forceReset ? forceReset.checked : false;
  const response = await fetch(`/api/projects/${projectId}/generate?reset=${isReset}`, { method: "POST" });
  if (!response.ok) {
    const err = await response.json();
    throw new Error(err.detail || "Failed to start generation");
  }
  statusBadge.textContent = "PROCESSING";
  statusBadge.classList.replace("text-slate-500", "text-sky-400");
  statusBadge.classList.add("animate-pulse", "border-sky-500/20", "bg-sky-500/10");
  logLine(isReset ? "Resetting cache and force-rendering..." : "Pipeline handshake successful. Rendering...", "success");
};


const pollStatus = async () => {
  if (!projectId) return;
  const response = await fetch(`/api/projects/${projectId}/status`);
  if (!response.ok) return;
  const data = await response.json();
  updateUIState(data);

  if (data.state === "done") {
    clearInterval(pollInterval);
    logLine("Neural processing complete.", "success");
    logLine("Video master ready for playback.", "success");
    
    videoPlaceholder.classList.add("hidden");
    videoPlayer.classList.remove("hidden");
    videoControls.classList.remove("hidden");
    videoPlayer.src = `/api/projects/${projectId}/video`;
    videoPlayer.play();
  }

  if (data.state === "failed") {
    clearInterval(pollInterval);
    statusBadge.textContent = "FAULT";
    statusBadge.classList.replace("text-sky-400", "text-rose-400");
    statusBadge.classList.remove("animate-pulse");

    if (errorAlert) {
        errorAlert.classList.remove("hidden");
        errorMessage.textContent = data.message;
    }
    logLine(`Engine Error: ${data.message}`, "error");
    setBusy(false);
  }
};

const setBusy = (busy) => {
  generateBtn.disabled = busy;
  const icon = generateBtn.querySelector("i");
  if (busy) {
    generateBtn.innerHTML = `<i data-lucide="loader-2" class="w-5 h-5 animate-spin"></i> Processing...`;
  } else {
    generateBtn.innerHTML = `<i data-lucide="wand-2" class="w-5 h-5"></i> Initialize Production`;
  }
  lucide.createIcons();
};

// Drag and Drop
dropZone.addEventListener("click", () => audioInput.click());
dropZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropZone.classList.add("border-sky-500", "bg-sky-500/5");
});
dropZone.addEventListener("dragleave", (e) => {
  e.preventDefault();
  dropZone.classList.remove("border-sky-500", "bg-sky-500/5");
});
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("border-sky-500", "bg-sky-500/5");
  if (e.dataTransfer.files.length) {
    selectedFile = e.dataTransfer.files[0];
    handleFileSelection();
  }
});

audioInput.addEventListener("change", (event) => {
  selectedFile = event.target.files[0];
  handleFileSelection();
});

const handleFileSelection = () => {
  if (selectedFile) {
    fileName.innerHTML = `<span class="text-sky-400 font-bold">${selectedFile.name}</span> (${(selectedFile.size / 1024 / 1024).toFixed(2)} MB)`;
    logLine(`Source loaded: ${selectedFile.name}`);
    generateBtn.disabled = false;
  } else {
    fileName.textContent = "Click to browse or drag audio file";
    generateBtn.disabled = true;
  }
};

generateBtn.addEventListener("click", async () => {
  try {
    if (!selectedFile) {
      logLine("No input detected. Please upload audio.", "error");
      return;
    }
    
    // Reset UI
    videoPlaceholder.classList.remove("hidden");
    videoPlayer.classList.add("hidden");
    videoControls.classList.add("hidden");
    log.innerHTML = "";
    
    setBusy(true);
    logLine("Warming up local environment...", "system");

    await createProject();
    await uploadAudio();
    await startGeneration();

    pollInterval = setInterval(pollStatus, 2000);
    await pollStatus();
  } catch (err) {
    logLine(`System Failure: ${err.message}`, "error");
    setBusy(false);
  }
});

