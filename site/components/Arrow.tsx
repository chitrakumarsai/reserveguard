// The kit's drawn arrow (svg.m-arrow), the last child of the primary button.
export function Arrow() {
  return (
    <svg className="m-arrow" viewBox="0 0 12 12" aria-hidden="true">
      <path className="m-arrow__stem" d="M1 6h8" />
      <path className="m-arrow__head" d="M4.5 2.5 8 6l-3.5 3.5" />
    </svg>
  );
}

export function Logo() {
  return (
    <a className="nav__logo" href="#top">
      <span className="mark" data-mark="ring" aria-hidden="true" />
      ReserveGuard
    </a>
  );
}
