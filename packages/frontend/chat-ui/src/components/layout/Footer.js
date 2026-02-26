import React from "react";
import { DEFAULT_FOOTER_CONFIG } from "../../styles/themeProvider";

const Footer = ({ chatTheme = null }) => {
  const footerConfig = { ...DEFAULT_FOOTER_CONFIG, ...chatTheme?.footer };

  if (footerConfig.visible === false) return null;

  const links = footerConfig.links || DEFAULT_FOOTER_CONFIG.links || [];

  return (
    <div
      className="hidden lg:flex flex-row justify-between items-center w-[100%] lg:px-[200px] px-[20px] h-[44px] mt-2"
      style={{ backdropFilter: "blur(6px)" }}
    >
      {links.map((link, i) => (
        <a
          key={link.label || i}
          href={link.href || '#'}
          target={link.external ? '_blank' : undefined}
          rel={link.external ? 'noopener noreferrer' : undefined}
          className="flex flex-col justify-center text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] md:text-[15px] text-[12px] leading-[15px] oxanium font-[400] text-center transition-colors"
        >
          {link.label}
        </a>
      ))}
      {footerConfig.poweredBy && (
        <span className="text-[var(--color-text-muted)] text-xs oxanium">
          {footerConfig.poweredBy}
        </span>
      )}
    </div>
  );
};

export default Footer;
