(function () {
    const videos = document.querySelectorAll('.invite-video');

    if (!videos.length) {
        return;
    }

    videos.forEach((video) => {
        video.volume = 0.5;
    });

    document.querySelectorAll('.sound-button').forEach((button) => {
        const wrap = button.closest('.video-wrap');
        const video = wrap ? wrap.querySelector('.invite-video') : null;

        if (!video) {
            return;
        }

        button.addEventListener('click', () => {
            video.muted = false;
            video.volume = 0.5;
            video.play().catch(() => {});
            button.classList.add('is-hidden');
        });
    });

    if (!('IntersectionObserver' in window)) {
        videos.forEach((video) => {
            video.play().catch(() => {});
        });
        return;
    }

    const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
            const video = entry.target;

            if (video.dataset.finished === 'true') {
                return;
            }

            if (entry.isIntersecting && entry.intersectionRatio >= 0.25) {
                video.play().catch(() => {});
            } else {
                video.pause();
            }
        });
    }, {
        threshold: [0, 0.25, 0.5],
    });

    videos.forEach((video) => {
        video.addEventListener('ended', () => {
            video.dataset.finished = 'true';
            observer.unobserve(video);
        });

        observer.observe(video);
    });
})();
