// /static/shared/toast.js - the legacy toast names. The renderer moved to
// shared/notify.js (the notification center, 2026-10-20): every toast is now
// a notification (a row, for warning and error) or a flash (success, info).
// Kept so plugin code written against this path keeps working; new code
// imports notify.js and says which it means.
export { showToast, showActionToast, dismissLater } from './notify.js';
