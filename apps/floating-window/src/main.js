const agent = document.querySelector("#floatingAgent");
const dragSurface = document.querySelector("#dragSurface");
const mascotButton = document.querySelector("#mascotButton");
const stateChip = document.querySelector("#stateChip");
const stateButtons = [...document.querySelectorAll("[data-state-button]")];

const stateOrder = ["idle", "observing", "analyzing", "privacy", "error"];
const stateLabels = {
  idle: "待命",
  observing: "观察",
  analyzing: "分析",
  privacy: "隐私",
  error: "异常"
};

let activeState = "idle";
let drag = null;

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function getAgentSize() {
  const rect = agent.getBoundingClientRect();
  return {
    width: rect.width,
    height: rect.height
  };
}

function setPosition(x, y, persist = true) {
  const { width, height } = getAgentSize();
  const maxX = window.innerWidth - width - 12;
  const maxY = window.innerHeight - height - 12;
  const nextX = clamp(x, 12, Math.max(12, maxX));
  const nextY = clamp(y, 12, Math.max(12, maxY));
  agent.style.setProperty("--agent-x", `${nextX}px`);
  agent.style.setProperty("--agent-y", `${nextY}px`);
  if (persist) {
    localStorage.setItem("floatingAgentPosition", JSON.stringify({ x: nextX, y: nextY }));
  }
}

function restorePosition() {
  const saved = localStorage.getItem("floatingAgentPosition");
  if (!saved) {
    const { width, height } = getAgentSize();
    setPosition(window.innerWidth - width - 32, window.innerHeight - height - 32, false);
    return;
  }

  try {
    const parsed = JSON.parse(saved);
    setPosition(Number(parsed.x), Number(parsed.y), false);
  } catch {
    localStorage.removeItem("floatingAgentPosition");
  }
}

function setState(nextState) {
  if (!stateOrder.includes(nextState)) {
    return;
  }

  activeState = nextState;
  agent.dataset.state = nextState;
  stateChip.textContent = stateLabels[nextState];

  for (const button of stateButtons) {
    button.classList.toggle("is-active", button.dataset.stateButton === nextState);
  }
}

function cycleState() {
  const currentIndex = stateOrder.indexOf(activeState);
  const nextIndex = (currentIndex + 1) % stateOrder.length;
  setState(stateOrder[nextIndex]);
}

dragSurface.addEventListener("pointerdown", (event) => {
  if (event.target.closest(".agent-toolbar")) {
    return;
  }

  const rect = agent.getBoundingClientRect();
  drag = {
    pointerId: event.pointerId,
    offsetX: event.clientX - rect.left,
    offsetY: event.clientY - rect.top,
    moved: false
  };
  agent.classList.add("is-dragging");
  dragSurface.setPointerCapture(event.pointerId);
});

dragSurface.addEventListener("pointermove", (event) => {
  if (!drag || drag.pointerId !== event.pointerId) {
    return;
  }

  const x = event.clientX - drag.offsetX;
  const y = event.clientY - drag.offsetY;
  if (Math.abs(x) > 2 || Math.abs(y) > 2) {
    drag.moved = true;
  }
  setPosition(x, y);
});

function endDrag(event) {
  if (!drag || drag.pointerId !== event.pointerId) {
    return;
  }
  dragSurface.releasePointerCapture(event.pointerId);
  agent.classList.remove("is-dragging");
  drag = null;
}

dragSurface.addEventListener("pointerup", endDrag);
dragSurface.addEventListener("pointercancel", endDrag);

mascotButton.addEventListener("click", () => {
  if (drag?.moved) {
    return;
  }
  cycleState();
});

for (const button of stateButtons) {
  button.addEventListener("click", () => {
    setState(button.dataset.stateButton);
  });
}

window.addEventListener("resize", () => {
  const rect = agent.getBoundingClientRect();
  setPosition(rect.left, rect.top, false);
});

restorePosition();
setState(activeState);
