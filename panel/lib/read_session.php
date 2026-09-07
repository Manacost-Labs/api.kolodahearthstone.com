<?php
declare(strict_types=1);

/** Call only after authorization and all session writes for this read request. */
function panel_finish_read_session(): void
{
    if (session_status() === PHP_SESSION_ACTIVE && !session_write_close()) {
        throw new RuntimeException('Unable to persist panel session.');
    }
}
