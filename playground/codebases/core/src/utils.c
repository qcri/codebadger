/*
 * utils.c - Common utilities implementation
 * 
 * Contains bounds checking examples (both with and without proper checks)
 * for testing find_bounds_checks tool.
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
 * Buffer copy WITH proper bounds checking - SAFE
 */
int buffer_copy_checked(void *dest, size_t dest_size, 
                        const void *src, size_t src_size)
{
    if (!dest || !src) {
        return ERR_INVALID_PARAM;
    }
    
    /* Proper bounds check BEFORE access */
    if (src_size > dest_size) {
        return ERR_BUFFER_OVERFLOW;
    }
    
    memcpy(dest, src, src_size);
    return ERR_SUCCESS;
}

/*
 * Buffer copy WITHOUT bounds checking - VULNERABLE
 * find_bounds_checks should flag this
 */
int buffer_copy_unchecked(void *dest, const void *src, size_t size)
{
    /* No bounds check - directly copies */
    memcpy(dest, src, size);  /* VULNERABLE: no size validation for dest */
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
 * Validate buffer access bounds - PROPER CHECK
 */
bool validate_buffer_access(const void *buf, size_t buf_size, 
                           size_t offset, size_t access_size)
{
    if (!buf) {
        return false;
    }
    
    /* Check for overflow in addition */
    if (offset > buf_size || access_size > buf_size) {
        return false;
    }
    
    if (offset + access_size > buf_size) {
        return false;
    }
    
    return true;
}

/*
 * Process buffer WITH bounds check - SAFE pattern
 * find_bounds_checks should find the check
 */
int process_with_bounds_check(char *buffer, size_t len, int index)
{
    /* Check BEFORE access */
    if (index < 0 || (size_t)index >= len) {
        return ERR_BUFFER_OVERFLOW;
    }
    
    buffer[index] = 'X';  /* Safe - bounds checked above */
    return ERR_SUCCESS;
}

/*
 * Process buffer WITHOUT bounds check first - VULNERABLE
 * find_bounds_checks should flag late check
 */
int process_without_bounds_check(char *buffer, size_t len, int index)
{
    buffer[index] = 'Y';  /* VULNERABLE: check comes AFTER access */
    
    /* Late check - too late! */
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
    arr[index] = value;  /* VULNERABLE: no bounds check */
}

/*
 * Caller that doesn't check before calling helper
 */
int process_array_unchecked(int *arr, size_t arr_size, int index, int value)
{
    /* No validation of index against arr_size */
    helper_array_write(arr, index, value);  /* Interprocedural vulnerability */
    return ERR_SUCCESS;
}

/*
 * Caller that checks before calling helper - SAFE
 */
int process_array_checked(int *arr, size_t arr_size, int index, int value)
{
    if (index < 0 || (size_t)index >= arr_size) {
        return ERR_BUFFER_OVERFLOW;
    }
    
    helper_array_write(arr, index, value);  /* Safe - checked above */
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
