/**
 * SteamIcons — Steam's own app-details action glyphs, reproduced.
 *
 * Our custom play row replaces Steam's native one for non-Steam
 * shortcuts, so its icon buttons sit next to (and are compared
 * against) vanilla Steam game pages. Generic react-icons glyphs
 * read as obviously foreign there: Font Awesome's `FaGamepad` is a
 * wide 640x512 pad with a d-pad, where Steam draws a squat 256x256
 * controller silhouette; `FaCog` has thin teeth against Steam's
 * heavier 36x36 gear.
 *
 * The path data below is copied verbatim off the live client — the
 * `MenuButton` / `ControllerConfigButton` pair in the Big Picture
 * app-details header (`aria-label="Configure Controller"` and
 * `"Manage"`). Captured with the `steam-debug` skill; note the BPM
 * DOM lives in the `BigPicture` CDP target, not `SharedJSContext`.
 *
 * Steam wraps its controller glyph in `SVGIcon_Button
 * SVGIcon_BigPicture` and hardcodes width/height attributes. Both are
 * dropped here: those are Steam-global CSS hooks that a client update
 * can restyle or remove, and sizing/colour must stay owned by our own
 * `.unifideck-icon-btn svg` rule in `play.css.ts`. `fill="currentColor"`
 * on the path is what lets the focus-inversion rule there repaint the
 * glyph dark on a white background.
 */
import { FC } from "react";

/** Steam's native "Configure Controller" glyph. */
export const SteamControllerIcon: FC = () => (
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" fill="none">
    <path
      fill="currentColor"
      d="M213.771,68.659c-4.108-7.066-46.007-7.231-49.293-7.231H128H91.522c-3.286,0-45.186,0.165-49.293,7.231 c-19.555,29.248-27.385,100.263-27.276,104.01c0.238,8.294,2.11,24.583,16.595,35.162c9.201,6.72,22.183,8.709,29.083,3.614 c4.989-3.682,11.995-19.224,19.061-32.204c7.064-12.981,9.202-11.174,12.98-12.159c3.78-0.986,36.066-0.74,36.066-0.74 s30.809-0.247,34.588,0.74c3.777,0.985,5.915-0.822,12.98,12.159c7.064,12.98,14.07,28.522,19.061,32.204 c6.9,5.095,19.882,3.106,29.083-3.614c14.485-10.58,16.356-26.868,16.595-35.162C241.154,168.922,233.325,97.906,213.771,68.659z M67.251,128.14c-14.974,0-27.112-12.137-27.112-27.111c0-14.975,12.137-27.112,27.112-27.112 c14.973,0,27.111,12.137,27.111,27.112C94.362,116.003,82.224,128.14,67.251,128.14z M188.749,128.14 c-14.974,0-27.111-12.137-27.111-27.111c0-14.975,12.138-27.112,27.111-27.112c14.973,0,27.111,12.137,27.111,27.112 C215.86,116.003,203.722,128.14,188.749,128.14z"
    />
  </svg>
);

/** Steam's native "Manage" (settings gear) glyph. */
export const SteamGearIcon: FC = () => (
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36" fill="none">
    <path
      fill="currentColor"
      d="M33 20.38V15.62L29.07 14.9C28.8121 14.015 28.453 13.1628 28 12.36L30.27 9.08L26.92 5.71L23.64 8C22.8372 7.54696 21.985 7.18793 21.1 6.93L20.38 3H15.62L14.9 6.93C14.015 7.18793 13.1628 7.54696 12.36 8L9.08 5.71L5.71 9.08L8 12.36C7.54696 13.1628 7.18793 14.015 6.93 14.9L3 15.62V20.38L6.93 21.1C7.18793 21.985 7.54696 22.8372 8 23.64L5.71 26.92L9.08 30.29L12.36 28C13.1637 28.4461 14.0159 28.7984 14.9 29.05L15.62 33H20.38L21.1 29.07C21.985 28.8121 22.8372 28.453 23.64 28L26.92 30.27L30.29 26.9L28 23.64C28.4461 22.8363 28.7984 21.9841 29.05 21.1L33 20.38ZM18 23C17.0111 23 16.0444 22.7068 15.2221 22.1573C14.3999 21.6079 13.759 20.827 13.3806 19.9134C13.0022 18.9998 12.9031 17.9945 13.0961 17.0245C13.289 16.0546 13.7652 15.1637 14.4645 14.4645C15.1637 13.7652 16.0546 13.289 17.0245 13.0961C17.9945 12.9031 18.9998 13.0022 19.9134 13.3806C20.827 13.759 21.6079 14.3999 22.1573 15.2221C22.7068 16.0444 23 17.0111 23 18C23 18.6566 22.8707 19.3068 22.6194 19.9134C22.3681 20.52 21.9998 21.0712 21.5355 21.5355C21.0712 21.9998 20.52 22.3681 19.9134 22.6194C19.3068 22.8707 18.6566 23 18 23Z"
    />
  </svg>
);
