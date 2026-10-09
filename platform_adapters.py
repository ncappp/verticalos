"""Explicit adapter registry. Registration is generic; unimplemented publishers stay disabled."""

ADAPTERS = {
    'TikTok': {
        'id': 'tiktok_android_v14',
        'label': 'TikTok',
        'state': 'PARTIAL',
        'note': 'Доступна проверенная Android-калибровка. Другие аккаунты/версии требуют отдельной проверки.',
    },
    'YouTube': {
        'id': 'youtube_oauth',
        'label': 'YouTube',
        'state': 'NOT_IMPLEMENTED',
        'note': 'Публикация и проверка результата ещё не реализованы; потребуется авторизация YouTube.',
    },
    'Instagram': {
        'id': 'instagram_graph',
        'label': 'Instagram',
        'state': 'NOT_IMPLEMENTED',
        'note': 'Публикация и проверка результата ещё не реализованы; требуется подходящий аккаунт и авторизация Meta.',
    },
    'VK': {
        'id': 'vk_video',
        'label': 'VK Видео',
        'state': 'NOT_IMPLEMENTED',
        'note': 'Публикация и проверка результата ещё не реализованы; потребуется авторизация VK.',
    },
}


def account_capability(account):
    adapter = ADAPTERS.get(account.get('platform'))
    # Existing calibration stays scoped to the tested account, not the whole software or a brand.
    calibrated = bool(
        adapter and adapter['id'] == 'tiktok_android_v14' and account.get('username') == '@redmaagi'
    )
    ready = calibrated and bool(account.get('device_id'))
    return {
        'adapter_id': adapter['id'] if adapter else None,
        'automation_ready': ready,
        'automation_note': (
            'Назначьте устройство'
            if calibrated and not ready
            else 'Проверенная калибровка доступна'
            if ready
            else adapter['note']
            if adapter
            else 'Адаптер не зарегистрирован'
        ),
    }
