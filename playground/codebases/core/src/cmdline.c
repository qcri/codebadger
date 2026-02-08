/*
 * cmdline.c - Command line processing
 * 
 * Contains command injection vulnerability patterns and
 * demonstrates taint flow from user input to system().
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "../include/utils.h"

/* Command buffer sizes */
#define CMD_BUFFER_SIZE 512
#define MAX_ARGS 32

/*
 * Read command from stdin - TAINT SOURCE
 */
char *cmdline_read_input(void)
{
    char *buffer = malloc(CMD_BUFFER_SIZE);
    if (!buffer) {
        return NULL;
    }
    
    /* TAINT SOURCE: fgets reads user input */
    if (fgets(buffer, CMD_BUFFER_SIZE, stdin) == NULL) {
        free(buffer);
        return NULL;
    }
    
    /* Remove trailing newline */
    size_t len = strlen(buffer);
    if (len > 0 && buffer[len - 1] == '\n') {
        buffer[len - 1] = '\0';
    }
    
    return buffer;
}

/*
 * Read command from file - TAINT SOURCE
 */
char *cmdline_read_from_file(const char *filepath)
{
    FILE *fp = fopen(filepath, "r");
    if (!fp) {
        return NULL;
    }
    
    char *buffer = malloc(CMD_BUFFER_SIZE);
    if (!buffer) {
        fclose(fp);
        return NULL;
    }
    
    /* TAINT SOURCE: fread from file */
    size_t n = fread(buffer, 1, CMD_BUFFER_SIZE - 1, fp);
    buffer[n] = '\0';
    fclose(fp);
    
    return buffer;
}

/*
 * Sanitize input - removes shell metacharacters
 * This is a sanitizer that should break some taint flows
 */
char *cmdline_sanitize(const char *input)
{
    if (!input) {
        return NULL;
    }
    
    size_t len = strlen(input);
    char *sanitized = malloc(len + 1);
    if (!sanitized) {
        return NULL;
    }
    
    size_t j = 0;
    for (size_t i = 0; i < len; i++) {
        char c = input[i];
        /* Only allow alphanumeric, space, and basic punctuation */
        if (isalnum(c) || c == ' ' || c == '.' || c == '-' || c == '_') {
            sanitized[j++] = c;
        }
        /* Skip shell metacharacters: ; | & $ ` etc */
    }
    sanitized[j] = '\0';
    
    return sanitized;
}

/*
 * Execute command directly - VULNERABLE
 * Taint flow: input -> system() without sanitization
 */
int cmdline_execute_unsafe(const char *cmd)
{
    if (!cmd) {
        return ERR_INVALID_PARAM;
    }
    
    /* VULNERABLE SINK: system() with unsanitized input */
    return system(cmd);  /* COMMAND INJECTION */
}

/*
 * Execute command with sanitization - SAFER
 * Taint flow broken by sanitizer
 */
int cmdline_execute_safe(const char *cmd)
{
    if (!cmd) {
        return ERR_INVALID_PARAM;
    }
    
    char *sanitized = cmdline_sanitize(cmd);
    if (!sanitized) {
        return ERR_OUT_OF_MEMORY;
    }
    
    /* Safer: sanitized before system() */
    int result = system(sanitized);
    
    free(sanitized);
    return result;
}

/*
 * Build and execute command - multi-step taint flow
 * Demonstrates taint propagation through string building
 */
int cmdline_build_and_execute(const char *base_cmd, const char *user_arg)
{
    if (!base_cmd || !user_arg) {
        return ERR_INVALID_PARAM;
    }
    
    char full_cmd[CMD_BUFFER_SIZE];
    
    /* Build command by concatenating user input */
    snprintf(full_cmd, sizeof(full_cmd), "%s %s", base_cmd, user_arg);
    
    /* VULNERABLE: user_arg flows to system() */
    return system(full_cmd);  /* COMMAND INJECTION via user_arg */
}

/*
 * Parse command into arguments
 */
int cmdline_parse_args(const char *cmdline, char **argv, int max_args)
{
    if (!cmdline || !argv || max_args <= 0) {
        return 0;
    }
    
    char *copy = safe_strdup(cmdline);
    if (!copy) {
        return 0;
    }
    
    int argc = 0;
    char *saveptr;
    char *token = strtok_r(copy, " \t", &saveptr);
    
    while (token && argc < max_args - 1) {
        argv[argc++] = safe_strdup(token);
        token = strtok_r(NULL, " \t", &saveptr);
    }
    argv[argc] = NULL;
    
    free(copy);
    return argc;
}

/*
 * Free parsed arguments
 */
void cmdline_free_args(char **argv, int argc)
{
    if (!argv) {
        return;
    }
    
    for (int i = 0; i < argc; i++) {
        if (argv[i]) {
            free(argv[i]);
        }
    }
}

/*
 * Execute with popen - VULNERABLE
 * Another command injection sink
 */
FILE *cmdline_popen_unsafe(const char *cmd)
{
    if (!cmd) {
        return NULL;
    }
    
    /* VULNERABLE SINK: popen with user-controlled command */
    return popen(cmd, "r");  /* COMMAND INJECTION */
}

/*
 * Read and execute interactive command - full taint flow
 * Flow: fgets() -> cmdline_build_and_execute() -> system()
 */
int cmdline_interactive_execute(const char *prompt)
{
    printf("%s", prompt ? prompt : "> ");
    fflush(stdout);
    
    /* TAINT SOURCE: Read from stdin */
    char *input = cmdline_read_input();
    if (!input) {
        return ERR_IO_ERROR;
    }
    
    /* VULNERABLE: Execute without sanitization */
    int result = cmdline_execute_unsafe(input);
    
    free(input);
    return result;
}

/*
 * Process command file - multi-level taint flow
 * Flow: fread() -> parse -> foreach line -> system()
 */
int cmdline_process_command_file(const char *filepath)
{
    if (!filepath) {
        return ERR_INVALID_PARAM;
    }
    
    /* TAINT SOURCE: Read commands from file */
    char *content = cmdline_read_from_file(filepath);
    if (!content) {
        return ERR_IO_ERROR;
    }
    
    /* Process each line as a command */
    char *saveptr;
    char *line = strtok_r(content, "\n", &saveptr);
    
    while (line) {
        /* Skip empty lines and comments */
        if (line[0] != '\0' && line[0] != '#') {
            /* VULNERABLE: Execute each line */
            cmdline_execute_unsafe(line);
        }
        line = strtok_r(NULL, "\n", &saveptr);
    }
    
    free(content);
    return ERR_SUCCESS;
}

/*
 * Helper for sprintf vulnerability
 * Takes user format and data
 */
static void format_user_message(char *buf, size_t size, 
                                const char *format, const char *data)
{
    /* VULNERABLE: sprintf with user-controlled format */
    sprintf(buf, format, data);  /* FORMAT STRING if format is tainted */
}

/*
 * Process user message with format - FORMAT STRING vulnerability
 */
int cmdline_format_message(const char *user_format, const char *user_data)
{
    if (!user_format) {
        return ERR_INVALID_PARAM;
    }
    
    char buffer[CMD_BUFFER_SIZE];
    
    /* VULNERABLE: User controls the format string */
    format_user_message(buffer, sizeof(buffer), user_format, 
                       user_data ? user_data : "");
    
    printf("%s\n", buffer);
    return ERR_SUCCESS;
}
