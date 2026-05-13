import React from "react";
import { DEFAULT_FOOTER_CONFIG } from "../../styles/themeProvider";
import { useNavigation } from "../../providers/NavigationProvider";
import { useNavigationActions } from "../../navigation/useNavigationActions";
import "./header-styles.css";

const isInternalHref = (value) => typeof value === "string" && value.startsWith("/");

const Footer = () => {
  const { footer: navFooter } = useNavigation();
  const handleNavigationItem = useNavigationActions();
  const footerConfig = { ...DEFAULT_FOOTER_CONFIG, ...navFooter };

  if (footerConfig.visible === false) return null;

  const links = footerConfig.links || DEFAULT_FOOTER_CONFIG.links || [];
  if (links.length === 0) return null;

  const footerClassName = footerConfig.hideOnMobile || footerConfig.mobileVisible === false
    ? "shell-footer shell-footer--hide-mobile"
    : "shell-footer";

  return (
    <footer className={footerClassName}>
      <div className="shell-footer-frame">
        {links.map((link, i) => (
          <React.Fragment key={link.label || i}>
            {i > 0 && <span className="shell-footer-divider" aria-hidden="true" />}
            {isInternalHref(link.href) && !link.external ? (
              <button
                type="button"
                onClick={() => handleNavigationItem({ path: link.href })}
                className="shell-footer-link"
              >
                {link.label}
              </button>
            ) : (
              <a
                href={link.href || "#"}
                target={link.external ? "_blank" : undefined}
                rel={link.external ? "noopener noreferrer" : undefined}
                className="shell-footer-link"
              >
                {link.label}
              </a>
            )}
          </React.Fragment>
        ))}
      </div>
    </footer>
  );
};

export default Footer;
