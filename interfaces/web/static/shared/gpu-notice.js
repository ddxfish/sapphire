// shared/gpu-notice.js - one line under the voice tabs when an NVIDIA card is
// there but this torch cannot use it (the Windows default torch has no CUDA).
// Reads the boot probe's verdict; says nothing when there is nothing to say.
export async function gpuNotice(el, selector) {
    const box = el.querySelector(selector);
    if (!box) return;
    try {
        const res = await fetch('/api/system/gpu');
        if (!res.ok) return;
        const g = await res.json();
        box.textContent = g.message ? `⚠ ${g.message}` : '';
    } catch (_) {
        // no notice beats a broken tab
    }
}
