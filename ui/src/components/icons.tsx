/** Shared inline SVG icons (lucide-react paths) — render identically on all platforms. */

export interface IconProps {
  color?: string;
  size?: number;
}

function svgProps(color: string | undefined, size = 16) {
  return {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: color,
    strokeWidth: 2,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  };
}

export function FolderOpenIcon({ color, size = 16 }: IconProps) {
  return (
     <svg {...svgProps(color, size)}>
       <path d="M6 14l1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.55 6a2 2 0 0 1-1.94 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2" />
     </svg>
   );
}

export function FolderIcon({ color, size = 16 }: IconProps) {
  return (
     <svg {...svgProps(color, size)}>
       <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" />
     </svg>
   );
}

export function FileIcon({ color, size = 16 }: IconProps) {
  return (
     <svg {...svgProps(color, size)}>
       <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" />
       <path d="M14 2v4a2 2 0 0 0 2 2h4" />
     </svg>
   );
}

export function EyeIcon({ color, size = 16 }: IconProps) {
  return (
     <svg {...svgProps(color, size)}>
       <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" />
       <circle cx="12" cy="12" r="3" />
     </svg>
   );
}

export function EyeOffIcon({ color, size = 16 }: IconProps) {
  return (
     <svg {...svgProps(color, size)}>
       <path d="M9.88 9.88a3 3 0 1 0 4.24 4.24" />
       <path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68" />
       <path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61" />
       <line x1="2" y1="2" x2="22" y2="22" />
     </svg>
   );
}

export function HomeIcon({ color, size = 16 }: IconProps) {
  return (
     <svg {...svgProps(color, size)}>
       <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
       <polyline points="9 22 9 12 15 12 15 22" />
     </svg>
   );
}

export function GlobeIcon({ color, size = 16 }: IconProps) {
  return (
     <svg {...svgProps(color, size)}>
       <circle cx="12" cy="12" r="10" />
       <line x1="2" y1="12" x2="22" y2="12" />
       <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
     </svg>
   );
}

export function RefreshIcon({ color, size = 16 }: IconProps) {
  return (
     <svg {...svgProps(color, size)}>
       <path d="M23 4v6h-6" />
       <path d="M1 20v-6h6" />
       <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
     </svg>
   );
}

export function SurfIcon({ color, size = 16 }: IconProps) {
  return (
     <svg {...svgProps(color, size)}>
       <path d="M2 21c3-2 5-2 8 0s5 2 8 0" />
       <path d="M12 3c-2 4-2 8 0 12" />
       <path d="M12 3c2 4 2 8 0 12" />
     </svg>
   );
}

/** Tab icons */
export function FileTextIcon({ color, size = 16 }: IconProps) {
  return (
     <svg {...svgProps(color, size)}>
       <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" />
       <path d="M14 2v4a2 2 0 0 0 2 2h4" />
       <line x1="8" y1="13" x2="16" y2="13" />
       <line x1="8" y1="17" x2="16" y2="17" />
       <line x1="8" y1="9" x2="10" y2="9" />
     </svg>
   );
}

export function MonitorIcon({ color, size = 16 }: IconProps) {
  return (
     <svg {...svgProps(color, size)}>
       <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
       <line x1="8" y1="21" x2="16" y2="21" />
       <line x1="12" y1="17" x2="12" y2="21" />
     </svg>
   );
}

export function TerminalIcon({ color, size = 16 }: IconProps) {
  return (
     <svg {...svgProps(color, size)}>
       <polyline points="4 17 10 11 4 5" />
       <line x1="12" y1="19" x2="20" y2="19" />
     </svg>
   );
}

export function CloseIcon({ color, size = 16 }: IconProps) {
  return (
     <svg {...svgProps(color, size)}>
        <line x1="18" y1="6" x2="6" y2="18" />
        <line x1="6" y1="6" x2="18" y2="18" />
      </svg>
    );
}

export function PlusIcon({ color, size = 16 }: IconProps) {
  return (
     <svg {...svgProps(color, size)}>
        <line x1="12" y1="5" x2="12" y2="19" />
        <line x1="5" y1="12" x2="19" y2="12" />
      </svg>
    );
}

export function ChevronDownIcon({ color, size = 16 }: IconProps) {
  return (
     <svg {...svgProps(color, size)}>
        <polyline points="6 9 12 15 18 9" />
      </svg>
    );
}
