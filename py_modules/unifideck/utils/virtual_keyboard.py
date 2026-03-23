"""
Virtual keyboard for the Microsoft auth page on Steam Deck.

Injected via CDP (Page.addScriptToEvaluateOnNewDocument) into the
Chromium auth browser.  Steam's overlay keyboard is unavailable
because Chromium auth runs outside of Steam.  No native Chromium API
exists for virtual keyboards on Linux.

The keyboard persists across page navigations (email -> password -> 2FA)
and auto-shows when an input or textarea gains focus.

Visual design
-------------
Dark translucent background with blur, gradient keys with press
animations, SVG icons for special keys, and a slide-up entrance
animation.  Styled to match the Steam Deck aesthetic.

Layout selection
----------------
The placeholder ``__UNIFIDECK_LOCALE__`` is replaced at injection time
with the user's BCP-47 locale (e.g. ``fr-FR``).  French locales get
AZERTY; everything else gets QWERTY.

Usage in ``microsoft.py``::

    from ..utils.virtual_keyboard import get_keyboard_js
    kb_js = get_keyboard_js(locale="fr-FR")
"""

# -- Keyboard JavaScript -------------------------------------------------------

_KEYBOARD_JS = r"""
(function() {
  'use strict';
  if (window.__unifideck_kb) return;
  window.__unifideck_kb = true;

  /* -- Inject stylesheet -------------------------------------------------- */

  var style = document.createElement('style');
  style.textContent = [
    '#unifideck-kb {',
    '  position:fixed; bottom:0; left:0; right:0; z-index:999999;',
    '  background: linear-gradient(180deg, rgba(20,22,30,0.97), rgba(12,14,20,0.99));',
    '  backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);',
    '  padding: 10px 6px 14px;',
    '  display: none;',
    '  touch-action: manipulation; user-select: none; -webkit-user-select: none;',
    '  border-top: 1px solid rgba(100,140,255,0.15);',
    '  box-shadow: 0 -8px 32px rgba(0,0,0,0.6), 0 -2px 8px rgba(100,140,255,0.05);',
    '  transform: translateY(100%);',
    '  transition: transform 0.25s cubic-bezier(0.16,1,0.3,1);',
    '}',
    '#unifideck-kb.visible { transform: translateY(0); }',
    '#unifideck-kb .kb-row {',
    '  display:flex; justify-content:center; gap:4px; margin:3px 0;',
    '}',
    '#unifideck-kb .kb-key {',
    '  min-width:36px; height:48px; font-size:17px; font-weight:500;',
    '  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;',
    '  border:none; border-radius:8px; color:#d4d8e0;',
    '  background: linear-gradient(180deg, rgba(45,50,65,0.9), rgba(35,38,50,0.95));',
    '  box-shadow: 0 1px 3px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.06);',
    '  touch-action:manipulation; -webkit-tap-highlight-color:transparent;',
    '  cursor:pointer; outline:none; padding:0;',
    '  transition: background 0.1s, transform 0.08s, box-shadow 0.1s;',
    '  display:flex; align-items:center; justify-content:center;',
    '}',
    '#unifideck-kb .kb-key:active,',
    '#unifideck-kb .kb-key.pressed {',
    '  background: linear-gradient(180deg, rgba(80,100,180,0.8), rgba(60,75,140,0.9));',
    '  transform: scale(0.94);',
    '  box-shadow: 0 0 0 rgba(0,0,0,0), inset 0 1px 2px rgba(0,0,0,0.3);',
    '  color: #fff;',
    '}',
    '#unifideck-kb .kb-special {',
    '  background: linear-gradient(180deg, rgba(55,60,80,0.9), rgba(40,44,58,0.95));',
    '  color: #8a90a0; font-size:14px; font-weight:600; letter-spacing:0.5px;',
    '}',
    '#unifideck-kb .kb-special:active,',
    '#unifideck-kb .kb-special.pressed {',
    '  background: linear-gradient(180deg, rgba(80,100,180,0.8), rgba(60,75,140,0.9));',
    '  color: #fff;',
    '}',
    '#unifideck-kb .kb-shift-active {',
    '  background: linear-gradient(180deg, rgba(70,90,160,0.9), rgba(55,70,130,0.95)) !important;',
    '  color: #a0b4ff !important;',
    '  box-shadow: 0 0 8px rgba(100,140,255,0.2), inset 0 1px 0 rgba(255,255,255,0.1);',
    '}',
    '#unifideck-kb .kb-space { flex:4; letter-spacing:2px; }',
    '#unifideck-kb .kb-wide  { flex:1.6; }',
    '#unifideck-kb .kb-enter {',
    '  background: linear-gradient(180deg, rgba(50,120,200,0.8), rgba(35,90,160,0.9));',
    '  color: #c8dbff;',
    '}',
    '#unifideck-kb .kb-enter:active {',
    '  background: linear-gradient(180deg, rgba(70,140,220,0.9), rgba(50,110,180,0.95));',
    '}',
    '#unifideck-kb .kb-badge {',
    '  position:absolute; top:-28px; right:12px;',
    '  font-size:11px; font-weight:600;',
    '  color: rgba(100,140,255,0.5);',
    '  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;',
    '  letter-spacing:1px; pointer-events:none;',
    '}',

  ].join('\n');
  document.head.appendChild(style);

  /* -- Layout definitions ------------------------------------------------- */

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

  /* -- Locale detection --------------------------------------------------- */

  /* Read locale from window variable (set by CDP before this script)
     or fall back to the placeholder replaced by Python at injection time. */
  var locale = window.__unifideck_locale || '__UNIFIDECK_LOCALE__';
  var lang = (locale || '').split('-')[0].toLowerCase();
  var layoutName = (lang === 'fr') ? 'azerty' : 'qwerty';
  var layout = LAYOUTS[layoutName];

  /* -- State --------------------------------------------------------------- */

  var shifted = false;
  var target  = null;

  /* -- DOM ----------------------------------------------------------------- */

  var overlay = document.createElement('div');
  overlay.id = 'unifideck-kb';

  /* -- SVG icons for special keys ----------------------------------------- */

  var SVG_BACK  = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12H3m6-6l-6 6 6 6"/></svg>';
  var SVG_ENTER = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M9 18l-6-6 6-6"/><path d="M21 6v6a2 2 0 01-2 2H3"/></svg>';
  var SVG_SHIFT = '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M12 3l-8 9h5v9h6v-9h5z"/></svg>';
  var SVG_SHIFT_ON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M12 3l-8 9h5v9h6v-9h5z"/><rect x="7" y="19" width="10" height="2" rx="1"/></svg>';

  /* -- Key handler -------------------------------------------------------- */

  function handleKey(ch) {
    if (!target) return;
    target.focus();

    switch (ch) {
      case 'BACK':
        document.execCommand('delete', false);
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

  /* -- Render ------------------------------------------------------------- */

  function render() {
    var rows = shifted ? layout.upper : layout.lower;
    overlay.innerHTML = '';

    rows.forEach(function(row) {
      var rowEl = document.createElement('div');
      rowEl.className = 'kb-row';

      row.forEach(function(k) {
        var btn = document.createElement('button');
        var special = k.length > 1;

        var cls = 'kb-key';
        if (special) cls += ' kb-special';
        if (k === 'SPACE')  cls += ' kb-space';
        if (k === 'ENTER')  cls += ' kb-enter';
        if (special && k !== 'SPACE') cls += ' kb-wide';
        if (k === 'SHIFT' && shifted) cls += ' kb-shift-active';
        btn.className = cls;

        if      (k === 'BACK')   btn.innerHTML = SVG_BACK;
        else if (k === 'ENTER')  btn.innerHTML = SVG_ENTER;
        else if (k === 'SHIFT')  btn.innerHTML = shifted ? SVG_SHIFT_ON : SVG_SHIFT;
        else if (k === 'SPACE')  btn.textContent = ' ';
        else                     btn.textContent = k;

        function press(e) {
          e.preventDefault();
          btn.classList.add('pressed');
          handleKey(k);
          setTimeout(function() { btn.classList.remove('pressed'); }, 120);
        }
        btn.addEventListener('touchstart', press, { passive: false });
        btn.addEventListener('mousedown',  press);
        rowEl.appendChild(btn);
      });
      overlay.appendChild(rowEl);
    });

    /* Badge */
    var badge = document.createElement('div');
    badge.className = 'kb-badge';
    badge.textContent = layoutName.toUpperCase();
    overlay.appendChild(badge);


  }

  /* -- Show / Hide with animation ----------------------------------------- */

  function showKB(el) {
    target = el;
    overlay.style.display = 'block';
    overlay.offsetHeight; /* reflow */
    overlay.classList.add('visible');
    setTimeout(function() {
      el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }, 280);
  }

  function hideKB() {
    overlay.classList.remove('visible');
    setTimeout(function() {
      overlay.style.display = 'none';
      target = null;
    }, 250);
  }

  /* -- Mount & listeners -------------------------------------------------- */

  function mount() {
    if (!document.body) {
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', mount);
      } else {
        setTimeout(mount, 50);
      }
      return;
    }
    render();
    document.body.appendChild(overlay);
  }
  mount();

  document.addEventListener('focusin', function(e) {
    var el = e.target, tag = el.tagName;
    var tp = (el.type || '').toLowerCase();
    if ((tag === 'INPUT' && tp !== 'hidden' && tp !== 'checkbox' && tp !== 'radio')
        || tag === 'TEXTAREA') {
      showKB(el);
    }
  }, true);

  document.addEventListener('focusout', function() {
    setTimeout(function() {
      var a = document.activeElement;
      if (!a || a === document.body) hideKB();
    }, 300);
  }, true);

  document.addEventListener('click', function(e) {
    if (overlay.style.display === 'none') return;
    var tag = e.target.tagName;
    if (tag !== 'INPUT' && tag !== 'TEXTAREA' && tag !== 'BUTTON'
        && !overlay.contains(e.target)) {
      hideKB();
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
    import re
    if not re.match(r'^[a-zA-Z]{2}(-[a-zA-Z]{2,4})?$', locale):
        locale = "en-US"
    return _KEYBOARD_JS.replace("__UNIFIDECK_LOCALE__", locale)
