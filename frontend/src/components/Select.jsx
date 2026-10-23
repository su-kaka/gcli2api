import { Check, ChevronDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";

/**
 * 自定义下拉选择器：选项面板由自身渲染（非原生 select 弹窗），
 * 面板与触发器左右边缘完全对齐；空间不足时自动向上弹出。
 */
export default function Select({ value, onChange, options, className = "", disabled = false }) {
  const [open, setOpen] = useState(false);
  const [placement, setPlacement] = useState({ dropUp: false, maxHeight: 240 });
  const rootRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const toggle = () => {
    if (disabled) return;
    if (!open && rootRef.current) {
      const rect = rootRef.current.getBoundingClientRect();
      const spaceBelow = window.innerHeight - rect.bottom;
      const spaceAbove = rect.top;
      // 面板高度按选项数量估算（每项约 40px，含内边距），上限 240px
      const desired = Math.min(options.length * 40 + 10, 240);
      if (spaceBelow >= desired || spaceBelow >= spaceAbove) {
        setPlacement({ dropUp: false, maxHeight: Math.min(spaceBelow - 8, desired) });
      } else {
        setPlacement({ dropUp: true, maxHeight: Math.min(spaceAbove - 8, desired) });
      }
    }
    setOpen((v) => !v);
  };

  const current = options.find((o) => o.value === value);

  return (
    <div ref={rootRef} className={`relative ${className}`}>
      <button
        type="button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={toggle}
        className={`w-full flex items-center justify-between gap-1 text-left bg-dark-800 border border-dark-600 rounded-lg px-3 py-2 text-gray-200 text-xs sm:text-sm focus:outline-none focus:ring-2 focus:ring-purple-500/50 ${
          open ? "ring-2 ring-purple-500/50 border-purple-500/50" : ""
        } ${disabled ? "opacity-40 cursor-not-allowed" : "cursor-pointer"}`}
      >
        <span className="truncate">{current ? current.label : ""}</span>
        <ChevronDown
          size={14}
          className={`shrink-0 text-gray-400 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <div
          role="listbox"
          style={{ maxHeight: Math.max(placement.maxHeight, 96) }}
          className={`absolute left-0 right-0 z-20 overflow-y-auto rounded-lg border border-dark-600 bg-dark-800 py-1 shadow-xl ${
            placement.dropUp ? "bottom-full mb-1" : "top-full mt-1"
          }`}
        >
          {options.map((o) => (
            <button
              key={o.value}
              type="button"
              role="option"
              aria-selected={o.value === value}
              onClick={() => {
                onChange(o.value);
                setOpen(false);
              }}
              className={`flex w-full items-center justify-between gap-2 px-3 py-2.5 sm:py-2 text-left text-xs sm:text-sm ${
                o.value === value
                  ? "bg-purple-600/20 text-purple-300"
                  : "text-gray-200 hover:bg-dark-700"
              }`}
            >
              <span className="truncate">{o.label}</span>
              {o.value === value && <Check size={14} className="shrink-0" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
