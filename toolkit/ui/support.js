/* GitHub support and update actions. Conversion traffic remains fully local. */
async function openExternal(target) {
  const result = await j("/api/open-external", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({target}),
  });
  if (!result.ok) setStatus("Couldn’t open GitHub. Please try again from the project page.");
  return result.ok;
}

async function sendFeedback() {
  if (await openExternal("feedback")) {
    setStatus("GitHub Issues opened. Please avoid sharing private footage or credentials.");
  }
}

async function openReleases() {
  if (await openExternal("releases")) setStatus("GitHub Releases opened in your browser.");
}

function showUpdateResult(result) {
  const modal = document.getElementById("mUpdate");
  const status = document.getElementById("updateStatus");
  modal.classList.add("on");
  if (!result || !result.ok) {
    status.textContent = result?.message || "Couldn’t check for updates. You can still open GitHub Releases.";
  } else if (result.update_available) {
    status.textContent = `Version ${result.latest_version} is available. You have ${result.current_version}.`;
  } else {
    status.textContent = `You’re up to date (version ${result.current_version}; latest release ${result.latest_version}).`;
  }
}

async function openUpdateCheck() {
  const modal = document.getElementById("mUpdate");
  const status = document.getElementById("updateStatus");
  modal.classList.add("on");
  status.textContent = "Checking GitHub Releases…";
  showUpdateResult(await j("/api/update"));
}

async function checkScheduledUpdateNotice(attempt = 0) {
  const state = await j("/api/update-notice");
  if (state.notice) {
    showUpdateResult(state.notice);
    return;
  }
  // The weekly query starts in a background thread so the app can open
  // immediately. Poll only briefly to surface that one result, if due.
  if (state.checking && attempt < 8) {
    setTimeout(() => checkScheduledUpdateNotice(attempt + 1), 750);
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => checkScheduledUpdateNotice());
} else {
  checkScheduledUpdateNotice();
}
