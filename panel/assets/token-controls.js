(() => {
    'use strict';
    document.querySelectorAll('[data-copy-token]').forEach((button) => {
        button.addEventListener('click', async () => {
            const secret = document.querySelector('[data-token-secret]');
            const status = document.querySelector('[data-copy-token-status]');
            const value = secret?.textContent?.trim() || '';
            if (!value || !navigator.clipboard) {
                if (status) status.textContent = 'Не удалось скопировать автоматически. Выделите токен вручную.';
                return;
            }
            try {
                await navigator.clipboard.writeText(value);
                if (status) status.textContent = 'Токен скопирован.';
                button.textContent = 'Скопировано';
            } catch (error) {
                if (status) status.textContent = 'Браузер запретил копирование. Выделите токен вручную.';
            }
        });
    });
})();
