/*
 * utils.h - Common utilities and macros for QEMU-like emulator
 * 
 * Provides buffer handling utilities, bounds checking macros,
 * and string processing helpers used across all modules.
 */

#ifndef UTILS_H
#define UTILS_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

/* Branch prediction hints */
#define likely(x)   __builtin_expect(!!(x), 1)
#define unlikely(x) __builtin_expect(!!(x), 0)

/* Buffer size limits */
#define SMALL_BUFFER_SIZE   64
#define MEDIUM_BUFFER_SIZE  256
#define LARGE_BUFFER_SIZE   1024
#define MAX_PATH_LENGTH     4096

/* Bounds checking macros */
#define BOUNDS_CHECK(idx, max) ((idx) >= 0 && (idx) < (max))
#define ARRAY_SIZE(arr) (sizeof(arr) / sizeof((arr)[0]))

/* Error codes */
#define ERR_SUCCESS         0
#define ERR_INVALID_PARAM  -1
#define ERR_OUT_OF_MEMORY  -2
#define ERR_BUFFER_OVERFLOW -3
#define ERR_INVALID_STATE  -4
#define ERR_NOT_FOUND      -5
#define ERR_IO_ERROR       -6

/* String utilities */
int safe_strcpy(char *dest, size_t dest_size, const char *src);
int safe_strcat(char *dest, size_t dest_size, const char *src);
char *safe_strdup(const char *src);

/* Buffer utilities */
int buffer_copy_checked(void *dest, size_t dest_size, 
                        const void *src, size_t src_size);
int buffer_copy_unchecked(void *dest, const void *src, size_t size);
void buffer_zero(void *buf, size_t size);

/* Bounds checking functions */
bool validate_buffer_access(const void *buf, size_t buf_size, 
                           size_t offset, size_t access_size);
int process_with_bounds_check(char *buffer, size_t len, int index);
int process_without_bounds_check(char *buffer, size_t len, int index);

/* Logging utilities */
void log_debug(const char *format, ...);
void log_error(const char *format, ...);
void log_info(const char *format, ...);

#endif /* UTILS_H */
