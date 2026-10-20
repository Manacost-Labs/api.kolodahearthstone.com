'use client';

import { useId, useState, type ComponentPropsWithRef, type ReactNode, type UIEvent } from 'react';
import { cx, ModalLayer, useControllableState, type ModalLayerProps } from '../primitives';
import { XIcon } from '../icons';
import { IconButton } from '../basic/IconButton';
import './Dialog.css';

export type DialogSize = 'sm' | 'md' | 'lg' | 'full';

export interface DialogProps extends Omit<ModalLayerProps, 'open' | 'onClose' | 'placement' | 'title' | 'children'> {
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Heading; becomes the dialog's accessible name. */
  title?: ReactNode;
  /** Supporting text; becomes the dialog's accessible description. */
  description?: ReactNode;
  /** Action bar under the body, usually buttons. */
  footer?: ReactNode;
  /** On phones (≤ 480px) every size becomes a bottom sheet, except small Glass dialogs, which stay iOS alerts. */
  size?: DialogSize;
  /** Show the × button (default true). */
  showClose?: boolean;
  /** Accessible name of the × button. */
  closeLabel?: string;
  /** Body content; it scrolls between the header and the footer when it overflows. */
  children?: ReactNode;
}

export function Dialog({
  open: openProp,
  defaultOpen = false,
  onOpenChange,
  title,
  description,
  footer,
  size = 'md',
  showClose = true,
  closeLabel = 'Close',
  className,
  layerClassName,
  children,
  ...rest
}: DialogProps) {
  const [open, setOpen] = useControllableState({ value: openProp, defaultValue: defaultOpen, onChange: onOpenChange });
  const [scrolled, setScrolled] = useState(false);
  const titleId = useId();
  const descriptionId = useId();

  // The body remounts on every opening, so its scroll state must start over too.
  if (!open && scrolled) setScrolled(false);

  return (
    <ModalLayer
      aria-labelledby={title ? titleId : undefined}
      aria-describedby={description ? descriptionId : undefined}
      {...rest}
      open={open}
      onClose={() => setOpen(false)}
      placement="center"
      layerClassName={cx('ui-dialog-layer', `ui-dialog-layer--${size}`, layerClassName)}
      className={cx('ui-floating ui-dialog', className)}
      data-size={size}
      data-closable={showClose || undefined}
      data-scrolled={scrolled || undefined}
    >
      {(title || description) && (
        <div className="ui-dialog__header">
          {title && (
            <h2 id={titleId} className="ui-dialog__title">
              {title}
            </h2>
          )}
          {description && (
            <div id={descriptionId} className="ui-dialog__description">
              {description}
            </div>
          )}
        </div>
      )}
      {children != null && children !== false && (
        <div
          className="ui-dialog__body"
          onScroll={(event: UIEvent<HTMLDivElement>) => setScrolled(event.currentTarget.scrollTop > 0)}
        >
          {children}
        </div>
      )}
      {footer && <DialogFooter>{footer}</DialogFooter>}
      {/* Last in DOM so initial focus lands on the content, not on the close button. */}
      {showClose && (
        <IconButton
          className="ui-dialog__close"
          size="sm"
          icon={<XIcon />}
          label={closeLabel}
          onClick={() => setOpen(false)}
        />
      )}
    </ModalLayer>
  );
}

export type DialogFooterProps = ComponentPropsWithRef<'div'>;

/**
 * The dialog action bar. `footer` renders it for you; place it yourself at the end of
 * the body when the actions must live inside it (e.g. the submit button of a `<form>`).
 */
export function DialogFooter({ className, ...rest }: DialogFooterProps) {
  return <div {...rest} className={cx('ui-dialog__footer', className)} />;
}
