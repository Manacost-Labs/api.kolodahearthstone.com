<?php
declare(strict_types=1);

/*
 * Required first by the php -S router of card_catalog_render_test.php (php -S skips auto_prepend_file for a router script): wraps the `file://` stream wrapper,
 * delegates every operation to the real filesystem and counts opens of render index files
 * (.../card-renders/{locale}.index.json). The total is appended to $COUNT_OPENS_FILE at request end.
 */
final class CountOpensWrapper
{
    public static int $opens = 0;
    public $context;
    private $handle = null;
    private $dir = null;

    private static function real(callable $fn): mixed
    {
        stream_wrapper_restore('file');
        try {
            return $fn();
        } finally {
            stream_wrapper_unregister('file');
            stream_wrapper_register('file', self::class);
        }
    }

    public function stream_open(string $path, string $mode, int $options, ?string &$opened): bool
    {
        if (preg_match('~/card-renders/[^/]+\.index\.json$~', $path)) self::$opens++;
        $quiet = ($options & STREAM_REPORT_ERRORS) === 0;
        $this->handle = self::real(fn () => $quiet ? @fopen($path, $mode) : fopen($path, $mode));
        return is_resource($this->handle);
    }
    public function stream_read(int $count): string|false { return fread($this->handle, $count); }
    public function stream_write(string $data): int { return (int)fwrite($this->handle, $data); }
    public function stream_eof(): bool { return feof($this->handle); }
    public function stream_tell(): int { return (int)ftell($this->handle); }
    public function stream_seek(int $offset, int $whence): bool { return fseek($this->handle, $offset, $whence) === 0; }
    public function stream_flush(): bool { return fflush($this->handle); }
    public function stream_close(): void { fclose($this->handle); }
    public function stream_stat(): array|false { return fstat($this->handle); }
    public function stream_lock(int $op): bool { return flock($this->handle, $op); }
    public function stream_truncate(int $size): bool { return ftruncate($this->handle, $size); }
    public function stream_set_option(int $option, int $arg1, ?int $arg2): bool { return false; }
    public function stream_metadata(string $path, int $option, mixed $value): bool
    {
        return (bool)self::real(fn () => match ($option) {
            STREAM_META_TOUCH => is_array($value) ? @touch($path, ...$value) : @touch($path),
            STREAM_META_ACCESS => @chmod($path, $value),
            default => false,
        });
    }
    public function url_stat(string $path, int $flags): array|false
    {
        return self::real(fn () => ($flags & STREAM_URL_STAT_QUIET) ? @stat($path) : stat($path));
    }
    public function unlink(string $path): bool { return self::real(fn () => @unlink($path)); }
    public function rename(string $from, string $to): bool { return self::real(fn () => @rename($from, $to)); }
    public function mkdir(string $path, int $mode, int $options): bool { return self::real(fn () => @mkdir($path, $mode, (bool)($options & STREAM_MKDIR_RECURSIVE))); }
    public function rmdir(string $path, int $options): bool { return self::real(fn () => @rmdir($path)); }
    public function dir_opendir(string $path, int $options): bool { $this->dir = self::real(fn () => @opendir($path)); return is_resource($this->dir); }
    public function dir_readdir(): string|false { return self::real(fn () => readdir($this->dir)); }
    public function dir_rewinddir(): bool { return self::real(fn () => rewinddir($this->dir)) !== false; }
    public function dir_closedir(): bool { self::real(fn () => closedir($this->dir)); return true; }
}

if (($file = getenv('COUNT_OPENS_FILE')) !== false && $file !== '') {
    stream_wrapper_unregister('file');
    stream_wrapper_register('file', CountOpensWrapper::class);
    register_shutdown_function(static function () use ($file): void {
        $n = CountOpensWrapper::$opens;
        stream_wrapper_restore('file');
        file_put_contents($file, "$n\n", FILE_APPEND);
    });
}
