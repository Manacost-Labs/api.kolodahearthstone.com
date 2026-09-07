(() => {
    const tooltip = document.getElementById('cardTooltip');
    const tooltipImage = tooltip?.querySelector('img');
    const tooltipText = tooltip?.querySelector('div');
    const fullscreen = document.getElementById('fullscreenCard');
    const fullscreenImage = fullscreen?.querySelector('img');
    const fullscreenVideo = fullscreen?.querySelector('video');
    const fullscreenMeta = fullscreen?.querySelector('.fullscreen-meta');
    const fullscreenClose = fullscreen?.querySelector('[data-fullscreen-close]');
    const workspace = document.querySelector('main.shell');
    let workspaceWasInert = false;
    let lastFullscreenTrigger = null;
    const moveTooltip = (event) => {
        if (!tooltip) return;
        const gap = 18;
        const rect = tooltip.getBoundingClientRect();
        let left = event.clientX + gap;
        let top = event.clientY + gap;
        if (left + rect.width > window.innerWidth - 12) left = event.clientX - rect.width - gap;
        if (top + rect.height > window.innerHeight - 12) top = window.innerHeight - rect.height - 12;
        tooltip.style.left = `${Math.max(12, left)}px`;
        tooltip.style.top = `${Math.max(12, top)}px`;
    };

    let tooltipTarget = null;
    const previewImageFromEvent = (event) => {
        const target = event.target;
        if (!(target instanceof Element)) return null;
        return target.closest('[data-preview]');
    };

    const showTooltip = (image, event) => {
        if (!tooltip || !tooltipImage || !tooltipText || tooltipTarget === image) return;
        tooltipTarget = image;
        if (image.dataset.previewType === 'video') {
            tooltipImage.src = '';
            tooltipImage.hidden = true;
        } else {
            tooltipImage.hidden = false;
            tooltipImage.src = image.dataset.preview || '';
        }
        tooltipText.textContent = image.dataset.tooltip || '';
        tooltip.hidden = false;
        moveTooltip(event);
    };

    const hideTooltip = () => {
        if (!tooltip || !tooltipImage) return;
        tooltipTarget = null;
        tooltip.hidden = true;
        tooltipImage.src = '';
        tooltipImage.hidden = false;
    };

    const openFullscreen = (image) => {
        if (!fullscreen || !fullscreenImage || !fullscreenVideo || !fullscreenMeta) return;
        if (fullscreen.hidden) workspaceWasInert = workspace?.inert || false;
        if (workspace) workspace.inert = true;
        hideTooltip();
        lastFullscreenTrigger = image instanceof HTMLElement ? image : null;
        const preview = image.dataset.preview || image.src;
        const isVideo = image.dataset.previewType === 'video' || /\.(webm|mp4)(?:\?|$)/i.test(preview);
        fullscreenImage.hidden = isVideo;
        fullscreenVideo.hidden = !isVideo;
        if (isVideo) {
            fullscreenImage.src = '';
            fullscreenVideo.src = preview;
            fullscreenVideo.play().catch(() => {});
        } else {
            fullscreenVideo.pause();
            fullscreenVideo.removeAttribute('src');
            fullscreenVideo.load();
            fullscreenImage.src = preview;
            fullscreenImage.alt = image.alt || 'Карта';
        }
        fullscreenMeta.textContent = image.dataset.tooltip || '';
        fullscreen.hidden = false;
        document.body.classList.add('modal-open');
        fullscreenClose?.focus({preventScroll: true});
    };

    const closeFullscreen = () => {
        if (!fullscreen || !fullscreenImage || !fullscreenVideo || !fullscreenMeta) return;
        fullscreen.hidden = true;
        fullscreenImage.src = '';
        fullscreenImage.hidden = false;
        fullscreenVideo.pause();
        fullscreenVideo.removeAttribute('src');
        fullscreenVideo.load();
        fullscreenVideo.hidden = true;
        fullscreenMeta.textContent = '';
        document.body.classList.remove('modal-open');
        if (workspace) workspace.inert = workspaceWasInert;
        const trigger = lastFullscreenTrigger;
        lastFullscreenTrigger = null;
        trigger?.focus({preventScroll: true});
    };

    document.addEventListener('mouseover', (event) => {
        const image = previewImageFromEvent(event);
        if (image) showTooltip(image, event);
    });
    document.addEventListener('mousemove', (event) => {
        if (tooltipTarget) moveTooltip(event);
    });
    document.addEventListener('mouseout', (event) => {
        if (!tooltipTarget) return;
        const nextTarget = event.relatedTarget;
        if (nextTarget instanceof Node && tooltipTarget.contains(nextTarget)) return;
        hideTooltip();
    });
    document.addEventListener('click', (event) => {
        const image = previewImageFromEvent(event);
        if (image) {
            event.preventDefault();
            openFullscreen(image);
        }
    });
    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        const image = previewImageFromEvent(event);
        if (!image) return;
        event.preventDefault();
        openFullscreen(image);
    });
    fullscreen?.addEventListener('click', (event) => {
        if (event.target === fullscreen || event.target.closest('[data-fullscreen-close]')) {
            closeFullscreen();
        }
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && fullscreen && !fullscreen.hidden) {
            closeFullscreen();
        }
        if (event.key === 'Tab' && fullscreen && !fullscreen.hidden) {
            const focusable = Array.from(fullscreen.querySelectorAll('button:not([disabled]), video:not([hidden])'))
                .filter((element) => element instanceof HTMLElement && element.getClientRects().length > 0);
            if (!focusable.length) {
                event.preventDefault();
                fullscreen.focus({preventScroll: true});
                return;
            }
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        }
    });
})();
