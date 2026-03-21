"""
Virtual keyboard for the Microsoft auth page on Steam Deck.

Injected via CDP (Page.addScriptToEvaluateOnNewDocument) into the
Chromium auth browser.  Steam's overlay keyboard is unavailable
because Chromium auth runs outside of Steam.  No native Chromium API
exists for virtual keyboards on Linux.

The keyboard persists across page navigations (email → password → 2FA)
and auto-shows when an input or textarea gains focus.

Layout selection
----------------
The placeholder ``__UNIFIDECK_LOCALE__`` is replaced at injection time
with the user's BCP-47 locale (e.g. ``fr-FR``).  French locales get
AZERTY; everything else gets QWERTY.

Usage in ``microsoft.py``::

    from ..utils.virtual_keyboard import get_keyboard_js
    kb_js = get_keyboard_js(locale="fr-FR")
"""

# ── Keyboard JavaScript ──────────────────────────────────────────────────────

_KEYBOARD_JS = r"""
(function() {
  'use strict';
  if (window.__unifideck_kb) return;
  window.__unifideck_kb = true;

  /* ── Layout definitions ─────────────────────────────────────────── */

  var LAYOUTS = {
    azerty: {
      lower: [
        ['1','2','3','4','5','6','7','8','9','0'],
        ['a','z','e','r','t','y','u','i','o','p'],
        ['q','s','d','f','g','h','j','k','l','m'],
        ['w','x','c','v','b','n',',',';',':','!'],
        ['SHIFT','@','.','SPACE','-','_','BACK','ENTER']
      ],
      upper: [
        ['&','\u00e9','"','\'','(','\u00a7','\u00e8','!','\u00e7','\u00e0'],
        ['A','Z','E','R','T','Y','U','I','O','P'],
        ['Q','S','D','F','G','H','J','K','L','M'],
        ['W','X','C','V','B','N','?','.','/','*'],
        ['SHIFT','#','~','SPACE','+','=','BACK','ENTER']
      ]
    },
    qwerty: {
      lower: [
        ['1','2','3','4','5','6','7','8','9','0'],
        ['q','w','e','r','t','y','u','i','o','p'],
        ['a','s','d','f','g','h','j','k','l'],
        ['z','x','c','v','b','n','m','@','.'],
        ['SHIFT','SPACE','-','_','BACK','ENTER']
      ],
      upper: [
        ['!','@','#','$','%','^','&','*','(',')'],
        ['Q','W','E','R','T','Y','U','I','O','P'],
        ['A','S','D','F','G','H','J','K','L'],
        ['Z','X','C','V','B','N','M','+','='],
        ['SHIFT','SPACE','?','/','BACK','ENTER']
      ]
    }
  };

  /* ── Locale detection ───────────────────────────────────────────── */

  var locale = '__UNIFIDECK_LOCALE__';
  var lang = (locale || '').split('-')[0].toLowerCase();
  var layoutName = (lang === 'fr') ? 'azerty' : 'qwerty';
  var layout = LAYOUTS[layoutName];

  /* ── State ──────────────────────────────────────────────────────── */

  var shifted = false;
  var target  = null;

  /* ── DOM: overlay container ─────────────────────────────────────── */

  var overlay = document.createElement('div');
  overlay.id = 'unifideck-kb';
  overlay.style.cssText =
    'position:fixed;bottom:0;left:0;right:0;z-index:999999;' +
    'background:#1a1a2e;padding:8px 4px;display:none;' +
    'touch-action:manipulation;user-select:none;-webkit-user-select:none;' +
    'border-top:2px solid #3a3a5a;box-shadow:0 -4px 20px rgba(0,0,0,0.5);';

  /* ── Key labels ─────────────────────────────────────────────────── */

  var LABELS = {
    'SPACE': '\u2423',
    'BACK':  '\u232B',
    'ENTER': '\u21B5',
    'SHIFT': '\u21E7'
  };
  var LABELS_SHIFTED = {
    'SHIFT': '\u2B06'
  };

  /* ── Key handler ────────────────────────────────────────────────── */

  function handleKey(ch) {
    if (!target) return;
    target.focus();

    switch (ch) {
      case 'BACK':
        var s = target.selectionStart || 0;
        if (s > 0) {
          var v = target.value;
          target.value = v.slice(0, s - 1) + v.slice(s);
          target.selectionStart = target.selectionEnd = s - 1;
        }
        break;

      case 'SPACE':
        document.execCommand('insertText', false, ' ');
        break;

      case 'ENTER':
        target.dispatchEvent(new KeyboardEvent('keydown', {
          key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true
        }));
        var form = target.closest('form');
        if (form) form.dispatchEvent(new Event('submit', { bubbles: true }));
        break;

      case 'SHIFT':
        shifted = !shifted;
        render();
        return;

      default:
        document.execCommand('insertText', false, ch);
        break;
    }

    target.dispatchEvent(new Event('input',  { bubbles: true }));
    target.dispatchEvent(new Event('change', { bubbles: true }));
  }

  /* ── Render keyboard rows ───────────────────────────────────────── */

  function render() {
    var rows = shifted ? layout.upper : layout.lower;
    overlay.innerHTML = '';

    rows.forEach(function(row) {
      var rowEl = document.createElement('div');
      rowEl.style.cssText =
        'display:flex;justify-content:center;gap:3px;margin:2px 0;';

      row.forEach(function(k) {
        var btn = document.createElement('button');

        /* Label */
        var lbl = shifted
          ? (LABELS_SHIFTED[k] || k)
          : (LABELS[k] || k);
        btn.textContent = lbl;

        /* Sizing */
        var flex = '';
        if (k === 'SPACE')     flex = 'flex:4;';
        else if (k.length > 1) flex = 'flex:1.5;';

        /* Active highlight for SHIFT */
        var bg = (k === 'SHIFT' && shifted) ? '#4a4a7a' : '#2a2a4a';

        btn.style.cssText = flex +
          'min-width:30px;height:44px;font-size:16px;font-weight:500;' +
          'border:1px solid #3a3a5a;border-radius:6px;color:#e0e0e0;' +
          'background:' + bg + ';' +
          'touch-action:manipulation;-webkit-tap-highlight-color:transparent;' +
          'cursor:pointer;outline:none;';

        btn.addEventListener('touchstart', function(e) {
          e.preventDefault(); handleKey(k);
        }, { passive: false });
        btn.addEventListener('mousedown', function(e) {
          e.preventDefault(); handleKey(k);
        });

        rowEl.appendChild(btn);
      });

      overlay.appendChild(rowEl);
    });

    /* Layout indicator */
    var badge = document.createElement('div');
    badge.textContent = layoutName.toUpperCase();
    badge.style.cssText =
      'position:absolute;bottom:6px;right:10px;font-size:10px;' +
      'color:#555;pointer-events:none;';
    overlay.appendChild(badge);
  }

  /* ── Initial render & mount ─────────────────────────────────────── */

  render();
  document.body.appendChild(overlay);

  /* ── Focus listeners: show/hide keyboard ─────────────────────────── */

  document.addEventListener('focusin', function(e) {
    var el = e.target;
    var tag = el.tagName;
    var tp  = (el.type || '').toLowerCase();
    if ((tag === 'INPUT' && tp !== 'hidden' && tp !== 'checkbox' && tp !== 'radio')
        || tag === 'TEXTAREA') {
      target = el;
      overlay.style.display = 'block';
      setTimeout(function() {
        el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      }, 100);
    }
  }, true);

  document.addEventListener('focusout', function() {
    setTimeout(function() {
      var active = document.activeElement;
      if (!active || active === document.body) {
        overlay.style.display = 'none';
        target = null;
      }
    }, 300);
  }, true);

  document.addEventListener('click', function(e) {
    if (overlay.style.display === 'none') return;
    var tag = e.target.tagName;
    if (tag !== 'INPUT' && tag !== 'TEXTAREA' && tag !== 'BUTTON'
        && !overlay.contains(e.target)) {
      overlay.style.display = 'none';
      target = null;
    }
  }, true);
})();
"""


def get_keyboard_js(locale: str = "en-US") -> str:
    """Return the virtual keyboard JavaScript with the given locale injected.

    Args:
        locale: BCP-47 locale string (e.g. "fr-FR", "en-US").
                French locales select AZERTY; all others select QWERTY.

    Returns:
        Ready-to-inject JavaScript string.
    """
    return _KEYBOARD_JS.replace("__UNIFIDECK_LOCALE__", locale)
