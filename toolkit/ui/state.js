/* Shared UI state. Behaviour stays in focused feature modules as they migrate. */
let SETTINGS = {};
let queue = [];
let running = false;
let wasRunning = false;
let mode = "HEVC (smaller, delivery)";
let strength = "Medium";
let rate = "Match source";
let clipSettingsIndex = null;
let queueReady = false, previewKey = "";
let nextQueueId = 1, thumbObserver = null;
let selectedRowIndex = null, activeTopProfile = "faithful", advancedMode = false, lastReport = null;
