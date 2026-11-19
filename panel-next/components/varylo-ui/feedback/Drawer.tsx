'use client';

import { useId, useRef, type PointerEvent, type ReactNode } from 'react';
import { cx, mergeRefs, ModalLayer, useControllableState, useLatest, type ModalLayerProps } from '../primitives';
import { XIcon } from '../icons';
import { IconButton } from '../basic/IconButton';
import { isDismissGesture } from './gesture';
import './Drawer.css';

export type DrawerPlacement = 'right' | 'left' | 'bottom';
export type DrawerSize = 'sm' | 'md' | 'lg' | 'full';

export interface DrawerProps extends Omit<ModalLayerProps, 'open' | 'onClose' | 'placement' | 'from' | 'title' | 'children'> {
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** `bottom` is a sheet with a grab handle that can be dragged down to dismiss. */
  placement?: DrawerPlacement;
  /** Width for side drawers, maximum height for bottom sheets. */
  size?: DrawerSize;
  /** Heading; becomes the drawer's accessible name. */
  title?: ReactNode;
  /** Supporting text under the title; becomes the drawer's accessible description. */
  description?: ReactNode;
  /** Sticky action bar under the scrollable body. */
  footer?: ReactNode;
  /** Show the × button (default true). */
  showClose?: boolean;
  /** Accessible name of the × button. */
  closeLabel?: string;
  /** Body content; it scrolls between the header and the footer when it overflows. */
  children?: ReactNode;
}

// Pull distance (px) that dismisses the sheet; a quick flick needs less.
const DRAG_DISMISS = 96;
const FLICK_VELOCITY = 0.6;

export function Drawer({
  open: openProp,
  defaultOpen = false,
  onOpenChange,
  placement = 'right',
  size = 'md',
  title,
  description,
  footer,
  showClose = true,
  closeLabel = 'Close',
  className,
  ref,
  children,
  ...rest
}: DrawerProps) {
  const [open, setOpen] = useControllableState({ value: openProp, defaultValue: defaultOpen, onChange: onOpenChange });
  const openLatest = useLatest(open);
  const titleId = useId();
  const descriptionId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ y: number; time: number; dy: number } | null>(null);

  // The sheet follows the finger directly (no React state per frame); releasing past the
  // threshold closes it and the exit animation continues from where it was dropped.
  const onHandleDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    drag.current = { y: event.clientY, time: event.timeStamp, dy: 0 };
    event.currentTarget.setPointerCapture(event.pointerId);
    panelRef.current?.setAttribute('data-dragging', '');
  };
  const onHandleMove = (event: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    const panel = panelRef.current;
    if (!d || !panel) return;
    const dy = event.clientY - d.y;
    // Pulling up only stretches a little.
    d.dy = dy > 0 ? dy : dy * 0.1;
    panel.style.transform = `translateY(${d.dy}px)`;
  };
  const onHandleUp = (event: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    const panel = panelRef.current;
    drag.current = null;
    if (!d || !panel) return;
    panel.removeAttribute('data-dragging');
    if (isDismissGesture(d.dy, event.timeStamp - d.time, Math.min(DRAG_DISMISS, panel.offsetHeight / 3), FLICK_VELOCITY)) {
      setOpen(false);
      // A controlling parent may refuse to close (unsaved changes): then the panel slides back instead of sticking.
      requestAnimationFrame(() => {
        if (openLatest.current) panel.style.transform = '';
      });
    } else {
      panel.style.transform = '';
    }
  };

  return (
    <ModalLayer
      aria-labelledby={title ? titleId : undefined}
      aria-describedby={description ? descriptionId : undefined}
      {...rest}
      ref={mergeRefs(panelRef, ref)}
      open={open}
      onClose={() => setOpen(false)}
      placement={placement}
      className={cx('ui-floating ui-drawer', className)}
      data-size={size}
      data-closable={showClose || undefined}
    >
      {placement === 'bottom' && (
        <div
          className="ui-drawer__handle"
          aria-hidden="true"
          onPointerDown={onHandleDown}
          onPointerMove={onHandleMove}
          onPointerUp={onHandleUp}
          onPointerCancel={onHandleUp}
        />
      )}
      {(title || description) && (
        <div className="ui-drawer__header">
          {title && (
            <h2 id={titleId} className="ui-drawer__title">
              {title}
            </h2>
          )}
          {description && (
            <div id={descriptionId} className="ui-drawer__description">
              {description}
            </div>
          )}
        </div>
      )}
      <div className="ui-drawer__body">{children}</div>
      {footer && <div className="ui-drawer__footer">{footer}</div>}
      {/* Last in DOM so initial focus lands on the content, not on the close button. */}
      {showClose && (
        <IconButton
          className="ui-drawer__close"
          size="sm"
          icon={<XIcon />}
          label={closeLabel}
          onClick={() => setOpen(false)}
        />
      )}
    </ModalLayer>
  );
}
