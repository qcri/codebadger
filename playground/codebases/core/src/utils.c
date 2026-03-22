/*
 * utils.c - Common utilities implementation
 * 
 * Provides string and buffer utilities, bounds-checked wrappers, and
 * the logging helpers used throughout the codebase.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include "../include/utils.h"

/*
 * Safe string copy with bounds checking
 */
int safe_strcpy(char *dest, size_t dest_size, const char *src)
{
    if (!dest || !src || dest_size == 0) {
        return ERR_INVALID_PARAM;
    }
    
    size_t src_len = strlen(src);
    if (src_len >= dest_size) {
        return ERR_BUFFER_OVERFLOW;
    }
    
    strcpy(dest, src);
    return ERR_SUCCESS;
}

/*
 * Safe string concatenation with bounds checking
 */
int safe_strcat(char *dest, size_t dest_size, const char *src)
{
    if (!dest || !src || dest_size == 0) {
        return ERR_INVALID_PARAM;
    }
    
    size_t dest_len = strlen(dest);
    size_t src_len = strlen(src);
    
    if (dest_len + src_len >= dest_size) {
        return ERR_BUFFER_OVERFLOW;
    }
    
    strcat(dest, src);
    return ERR_SUCCESS;
}

/*
 * Safe string duplication
 */
char *safe_strdup(const char *src)
{
    if (!src) {
        return NULL;
    }
    
    size_t len = strlen(src) + 1;
    char *dup = malloc(len);
    if (dup) {
        memcpy(dup, src, len);
    }
    return dup;
}

/*
 * Copy src into dest, returning an error if src_size exceeds dest_size.
 */
int buffer_copy_checked(void *dest, size_t dest_size, 
                        const void *src, size_t src_size)
{
    if (!dest || !src) {
        return ERR_INVALID_PARAM;
    }
    
    if (src_size > dest_size) {
        return ERR_BUFFER_OVERFLOW;
    }
    
    memcpy(dest, src, src_size);
    return ERR_SUCCESS;
}

/*
 * Copy src into dest without constraining to dest's declared capacity.
 * Returns ERR_SUCCESS; callers are responsible for ensuring dest is large enough.
 */
int buffer_copy_unchecked(void *dest, const void *src, size_t size)
{
    /* No bounds check - directly copies */
    memcpy(dest, src, size);
    return ERR_SUCCESS;
}

/*
 * Zero buffer contents
 */
void buffer_zero(void *buf, size_t size)
{
    if (buf && size > 0) {
        memset(buf, 0, size);
    }
}

/*
 * Return true if a [offset, offset+access_size) window fits within buf_size.
 */
bool validate_buffer_access(const void *buf, size_t buf_size, 
                           size_t offset, size_t access_size)
{
    if (!buf) {
        return false;
    }
    
    if (offset > buf_size || access_size > buf_size) {
        return false;
    }
    
    if (offset + access_size > buf_size) {
        return false;
    }
    
    return true;
}

/*
 * Write to buffer[index] after verifying index is in range.
 */
int process_with_bounds_check(char *buffer, size_t len, int index)
{
    if (index < 0 || (size_t)index >= len) {
        return ERR_BUFFER_OVERFLOW;
    }
    
    buffer[index] = 'X';
    return ERR_SUCCESS;
}

/*
 * Write to buffer[index], then validate the index.
 */
int process_without_bounds_check(char *buffer, size_t len, int index)
{
    buffer[index] = 'Y';
    
    if (index < 0 || (size_t)index >= len) {
        return ERR_BUFFER_OVERFLOW;
    }
    
    return ERR_SUCCESS;
}

/*
 * Helper that does unchecked array write - for interprocedural testing
 */
static void helper_array_write(int *arr, int index, int value)
{
    arr[index] = value;
}

/*
 * Write value to arr[index] without prior range validation.
 */
int process_array_unchecked(int *arr, size_t arr_size, int index, int value)
{
    helper_array_write(arr, index, value);
    return ERR_SUCCESS;
}

/*
 * Validate index against arr_size, then write value to arr[index].
 */
int process_array_checked(int *arr, size_t arr_size, int index, int value)
{
    if (index < 0 || (size_t)index >= arr_size) {
        return ERR_BUFFER_OVERFLOW;
    }
    
    helper_array_write(arr, index, value);
    return ERR_SUCCESS;
}

/*
 * Logging utilities
 */
void log_debug(const char *format, ...)
{
    va_list args;
    va_start(args, format);
    printf("[DEBUG] ");
    vprintf(format, args);
    printf("\n");
    va_end(args);
}

void log_error(const char *format, ...)
{
    va_list args;
    va_start(args, format);
    fprintf(stderr, "[ERROR] ");
    vfprintf(stderr, format, args);
    fprintf(stderr, "\n");
    va_end(args);
}

void log_info(const char *format, ...)
{
    va_list args;
    va_start(args, format);
    printf("[INFO] ");
    vprintf(format, args);
    printf("\n");
    va_end(args);
}
