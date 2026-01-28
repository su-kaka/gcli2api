import json
import os

class I18nManager:
    _instance = None
    _translations = {}
    _fallback_translations = {}
    _current_lang = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(I18nManager, cls).__new__(cls)
        return cls._instance

    async def _load_translations(self):
        try:
            from config import get_i18n_lang
            lang = await get_i18n_lang()
        except ImportError:
            lang = os.getenv("I18N_LANG", "zh")

        if self._current_lang == lang:
            return

        # Load fallback (Chinese)
        if not self._fallback_translations:
            zh_path = os.path.join(os.getcwd(), 'i18n', 'zh.json')
            if os.path.exists(zh_path):
                with open(zh_path, 'r', encoding='utf-8') as f:
                    self._fallback_translations = json.load(f)

        # Load target language
        if lang == 'zh':
            self._translations = self._fallback_translations
        else:
            lang_path = os.path.join(os.getcwd(), 'i18n', f'{lang}.json')
            if os.path.exists(lang_path):
                with open(lang_path, 'r', encoding='utf-8') as f:
                    self._translations = json.load(f)
            else:
                self._translations = self._fallback_translations
        
        self._current_lang = lang

    async def translate(self, key: str, **kwargs) -> str:
        await self._load_translations()
        return self._translate_sync(key, **kwargs)

    def _translate_sync(self, key: str, **kwargs) -> str:
        # Get translation or fallback to zh, then to key itself
        text = self._translations.get(key, self._fallback_translations.get(key, key))
        
        # Handle variable substitution if needed
        if kwargs:
            try:
                return text.format(**kwargs)
            except KeyError:
                return text
        return text

    def load_sync(self):
        """Synchronous load for use in non-async contexts"""
        if self._current_lang:
            return
            
        # Try to read from environment directly or fallback to 'zh'
        lang = os.getenv("I18N_LANG", "zh")
        
        # Load fallback (Chinese)
        zh_path = os.path.join(os.getcwd(), 'i18n', 'zh.json')
        if os.path.exists(zh_path):
            with open(zh_path, 'r', encoding='utf-8') as f:
                self._fallback_translations = json.load(f)

        # Load target language
        if lang == 'zh':
            self._translations = self._fallback_translations
        else:
            lang_path = os.path.join(os.getcwd(), 'i18n', f'{lang}.json')
            if os.path.exists(lang_path):
                with open(lang_path, 'r', encoding='utf-8') as f:
                    self._translations = json.load(f)
            else:
                self._translations = self._fallback_translations
        
        self._current_lang = lang

i18n_manager = I18nManager()

async def t(key: str, **kwargs) -> str:
    """Async translation helper function"""
    return await i18n_manager.translate(key, **kwargs)

def ts(key: str, **kwargs) -> str:
    """Synchronous translation helper function"""
    i18n_manager.load_sync()
    return i18n_manager._translate_sync(key, **kwargs)
