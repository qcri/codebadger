/*
 * config.c - Configuration parsing implementation
 * 
 * Parses INI-style configuration files and environment overrides.
 * Exposes get/set accessors and script execution for subsystem startup.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include "../include/config.h"

/*
 * Create configuration context
 */
ConfigContext *config_create(void)
{
    ConfigContext *ctx = malloc(sizeof(ConfigContext));
    if (!ctx) {
        return NULL;
    }
    
    ctx->entries = NULL;
    ctx->entry_count = 0;
    ctx->is_loaded = false;
    memset(ctx->config_file_path, 0, sizeof(ctx->config_file_path));
    
    return ctx;
}

/*
 * Destroy configuration context
 */
void config_destroy(ConfigContext *ctx)
{
    if (!ctx) {
        return;
    }
    
    /* Free all entries */
    ConfigEntry *entry = ctx->entries;
    while (entry) {
        ConfigEntry *next = entry->next;
        free(entry);
        entry = next;
    }
    
    free(ctx);
}

/*
 * Initialize configuration context
 */
int config_init(ConfigContext *ctx)
{
    if (!ctx) {
        return ERR_INVALID_PARAM;
    }
    
    ctx->is_loaded = false;
    return ERR_SUCCESS;
}

/*
 * Parse a single line of config: "key = value"
 * Part of deep call chain for config loading
 */
int config_parse_line(char *line, char *key, char *value)
{
    if (!line || !key || !value) {
        return ERR_INVALID_PARAM;
    }
    
    /* Find the '=' separator */
    char *eq = strchr(line, '=');
    if (!eq) {
        return ERR_INVALID_PARAM;
    }
    
    /* Extract key (trim spaces) */
    size_t key_len = eq - line;
    if (key_len >= MAX_KEY_LENGTH) {
        key_len = MAX_KEY_LENGTH - 1;
    }
    strncpy(key, line, key_len);
    key[key_len] = '\0';
    
    /* Trim trailing spaces from key */
    while (key_len > 0 && key[key_len - 1] == ' ') {
        key[--key_len] = '\0';
    }
    
    /* Extract value (skip leading spaces) */
    char *val_start = eq + 1;
    while (*val_start == ' ') {
        val_start++;
    }
    
    safe_strcpy(value, MAX_VALUE_LENGTH, val_start);
    
    /* Remove trailing newline */
    size_t val_len = strlen(value);
    if (val_len > 0 && value[val_len - 1] == '\n') {
        value[val_len - 1] = '\0';
    }
    
    return ERR_SUCCESS;
}

/*
 * Validate config entry (step 2 in deep chain)
 */
int config_validate_entry(const char *key, const char *value)
{
    if (!key || !value) {
        return ERR_INVALID_PARAM;
    }
    
    /* Key must not be empty */
    if (strlen(key) == 0) {
        return ERR_INVALID_PARAM;
    }
    
    /* Value length check */
    if (strlen(value) >= MAX_VALUE_LENGTH) {
        return ERR_BUFFER_OVERFLOW;
    }
    
    return ERR_SUCCESS;
}

/*
 * Process config entry (step 3 in deep chain)
 */
int config_process_entry(ConfigContext *ctx, const char *key, const char *value)
{
    if (!ctx || !key || !value) {
        return ERR_INVALID_PARAM;
    }
    
    /* Create new entry */
    ConfigEntry *entry = malloc(sizeof(ConfigEntry));
    if (!entry) {
        return ERR_OUT_OF_MEMORY;
    }
    
    safe_strcpy(entry->key, sizeof(entry->key), key);
    safe_strcpy(entry->value, sizeof(entry->value), value);
    entry->type = CONFIG_TYPE_STRING;
    entry->next = NULL;
    
    return config_apply_entry(ctx, entry);
}

/*
 * Apply config entry (step 4 in deep chain)
 */
int config_apply_entry(ConfigContext *ctx, ConfigEntry *entry)
{
    if (!ctx || !entry) {
        return ERR_INVALID_PARAM;
    }
    
    /* Add to front of list */
    entry->next = ctx->entries;
    ctx->entries = entry;
    ctx->entry_count++;
    
    return ERR_SUCCESS;
}

/*
 * Finalize loading (step 5 in deep chain)
 */
int config_finalize_loading(ConfigContext *ctx)
{
    if (!ctx) {
        return ERR_INVALID_PARAM;
    }
    
    ctx->is_loaded = true;
    log_info("Configuration loaded: %zu entries", ctx->entry_count);
    
    return ERR_SUCCESS;
}

/*
 * Load configuration from a file.  Each key=value line is parsed,
 * validated, and applied.  The call chain:
 *   load_file -> parse_line -> validate_entry -> process_entry -> finalize
 */
int config_load_file(ConfigContext *ctx, const char *filepath)
{
    if (!ctx || !filepath) {
        return ERR_INVALID_PARAM;
    }
    
    FILE *fp = fopen(filepath, "r");
    if (!fp) {
        return ERR_IO_ERROR;
    }
    
    safe_strcpy(ctx->config_file_path, sizeof(ctx->config_file_path), filepath);
    
    char line[MAX_VALUE_LENGTH];
    char key[MAX_KEY_LENGTH];
    char value[MAX_VALUE_LENGTH];
    
    while (fgets(line, sizeof(line), fp)) {
        /* Skip comments and empty lines */
        if (line[0] == '#' || line[0] == '\n') {
            continue;
        }
        
        /* Parse line (step 1) */
        if (config_parse_line(line, key, value) != ERR_SUCCESS) {
            continue;
        }
        
        /* Validate entry (step 2) */
        if (config_validate_entry(key, value) != ERR_SUCCESS) {
            continue;
        }
        
        /* Process entry (step 3 -> step 4) */
        config_process_entry(ctx, key, value);
    }
    
    fclose(fp);
    
    /* Finalize (step 5) */
    return config_finalize_loading(ctx);
}

/*
 * Load configuration from the path given by CONFIG_FILE_PATH, if set.
 */
int config_load_from_env(ConfigContext *ctx)
{
    if (!ctx) {
        return ERR_INVALID_PARAM;
    }
    
    char *config_path = getenv("CONFIG_FILE_PATH");
    if (config_path) {
        return config_load_file(ctx, config_path);
    }
    
    return ERR_SUCCESS;
}

/*
 * Parse a configuration block held in an in-memory buffer.
 */
int config_parse_buffer(ConfigContext *ctx, const char *buffer, size_t size)
{
    if (!ctx || !buffer) {
        return ERR_INVALID_PARAM;
    }
    
    /* Make a mutable copy */
    char *buf_copy = malloc(size + 1);
    if (!buf_copy) {
        return ERR_OUT_OF_MEMORY;
    }
    
    memcpy(buf_copy, buffer, size);
    buf_copy[size] = '\0';
    
    /* Parse each line */
    char *saveptr;
    char *line = strtok_r(buf_copy, "\n", &saveptr);
    
    char key[MAX_KEY_LENGTH];
    char value[MAX_VALUE_LENGTH];
    
    while (line) {
        if (config_parse_line(line, key, value) == ERR_SUCCESS) {
            if (config_validate_entry(key, value) == ERR_SUCCESS) {
                config_process_entry(ctx, key, value);
            }
        }
        line = strtok_r(NULL, "\n", &saveptr);
    }
    
    free(buf_copy);
    return config_finalize_loading(ctx);
}

/*
 * Get string value
 */
const char *config_get_string(ConfigContext *ctx, const char *key)
{
    if (!ctx || !key) {
        return NULL;
    }
    
    ConfigEntry *entry = ctx->entries;
    while (entry) {
        if (strcmp(entry->key, key) == 0) {
            return entry->value;
        }
        entry = entry->next;
    }
    
    return NULL;
}

/*
 * Get integer value
 */
int config_get_int(ConfigContext *ctx, const char *key, int default_val)
{
    const char *value = config_get_string(ctx, key);
    if (value) {
        return atoi(value);
    }
    return default_val;
}

/*
 * Get boolean value
 */
bool config_get_bool(ConfigContext *ctx, const char *key, bool default_val)
{
    const char *value = config_get_string(ctx, key);
    if (value) {
        if (strcmp(value, "true") == 0 || strcmp(value, "1") == 0 ||
            strcmp(value, "yes") == 0) {
            return true;
        }
        if (strcmp(value, "false") == 0 || strcmp(value, "0") == 0 ||
            strcmp(value, "no") == 0) {
            return false;
        }
    }
    return default_val;
}

/*
 * Get path value
 */
const char *config_get_path(ConfigContext *ctx, const char *key)
{
    return config_get_string(ctx, key);
}

/*
 * Set string value
 */
int config_set_string(ConfigContext *ctx, const char *key, const char *value)
{
    if (!ctx || !key || !value) {
        return ERR_INVALID_PARAM;
    }
    
    /* Check if key already exists */
    ConfigEntry *entry = ctx->entries;
    while (entry) {
        if (strcmp(entry->key, key) == 0) {
            safe_strcpy(entry->value, sizeof(entry->value), value);
            return ERR_SUCCESS;
        }
        entry = entry->next;
    }
    
    /* Create new entry */
    return config_process_entry(ctx, key, value);
}

/*
 * Set integer value
 */
int config_set_int(ConfigContext *ctx, const char *key, int value)
{
    char str_value[32];
    snprintf(str_value, sizeof(str_value), "%d", value);
    return config_set_string(ctx, key, str_value);
}

/*
 * Emit a configuration log line using the supplied format string.
 */
void config_log_entry(const char *format)
{
    printf(format);
}

/*
 * Print the value stored under key, using it as a printf format string
 * to support %-style message templates in configuration.
 */
int config_print_value(ConfigContext *ctx, const char *key)
{
    if (!ctx || !key) {
        return ERR_INVALID_PARAM;
    }
    
    const char *value = config_get_string(ctx, key);
    if (!value) {
        return ERR_NOT_FOUND;
    }
    
    printf(value);
    
    /* Also through helper */
    config_log_entry(value);
    
    return ERR_SUCCESS;
}

/*
 * Open the file whose path is stored under key in the configuration.
 * Returns the file descriptor, or a negative error code.
 */
int config_open_path(ConfigContext *ctx, const char *key)
{
    if (!ctx || !key) {
        return ERR_INVALID_PARAM;
    }
    
    const char *path = config_get_path(ctx, key);
    if (!path) {
        return ERR_NOT_FOUND;
    }
    
    int fd = open(path, O_RDONLY);
    
    return fd;
}

/*
 * Invoke the script whose name is stored under key in the configuration.
 */
int config_execute_script(ConfigContext *ctx, const char *key)
{
    if (!ctx || !key) {
        return ERR_INVALID_PARAM;
    }
    
    const char *script = config_get_string(ctx, key);
    if (!script) {
        return ERR_NOT_FOUND;
    }
    
    return system(script);
}
