"""The ShivyC parser. It's written entirely by hand because automatic parser
generators are no fun.

"""
from collections import namedtuple

import ast
from errors import ParserError
import errors
from tokens import Token
import token_kinds

class MatchError(Exception):
    """Raised by match_tokens to indicate a failure to match the token_kinds
    expected. For parser internal use only.
    """
    pass

class Parser:
    """Provides the parser functionality to convert a list of tokens into an
    AST.

    Each internal function parse_* corresponds to a unique non-terminal symbol
    in the C grammar. It parses self.tokens beginning at the given index to try
    to match a grammar rule that generates the desired symbol. If a match is
    found, it returns a tuple (Node, index) where Node is an AST node for that
    match and index is one more than that of the last token consumed in that
    parse. If no match is not found, raises a ParserError.

    Whenever a call to a parse_* function raises a ParserError, the calling
    function must either catch the exception and log it (using self.log_error),
    or pass the exception on to the caller. A function takes the first approach
    if there are other possible parse paths to consider, and the second approach
    if the function cannot parse the entity from the tokens.

    tokens (List(Token)) - The list of tokens to be parsed
    best_error (ParserError) - The "best error" encountered thusfar. That is,
    out of all the errors encountered thusfar, the one that occurred after
    succesfully parsing the most tokens.

    """
    
    def __init__(self, tokens):
        self.tokens = tokens
        self.best_error = None

    def parse(self):
        """Parse the provided list of tokens into an abstract syntax tree (AST)

        returns (Node) - The root node of the generated AST"""

        try:
            node, index = self.parse_main(0)
        except ParserError as e:
            self.log_error(e)
            raise self.best_error

        # Ensure there's no tokens left at after the main function
        if self.tokens[index:]:
            err = "unexpected token"
            raise ParserError(err, index, self.tokens, ParserError.AT)

        return node
        
    def parse_main(self, index):
        """Ex: int main() { return 4; } """

        kinds_before = [token_kinds.int_kw, token_kinds.main,
                        token_kinds.open_paren, token_kinds.close_paren,
                        token_kinds.open_brack]
        try:
            index = self.match_tokens(index, kinds_before)
        except MatchError:
            err = "expected main function starting"
            raise ParserError(err, index, self.tokens, ParserError.AT)

        nodes = []
        while True:
            try:
                node, index = self.parse_statement(index)
                nodes.append(node)
                continue
            except ParserError as e:
                self.log_error(e)

            try:
                node, index = self.parse_declaration(index)
                nodes.append(node)
                continue
            except ParserError as e:
                self.log_error(e)
                # When all of our parsing attempts fail, break out of the loop
                break

        try:
            index = self.match_token(index, token_kinds.close_brack)
        except MatchError:
            err = "expected closing brace"
            raise ParserError(err, index, self.tokens, ParserError.GOT)
            
        return (ast.MainNode(nodes), index)

    def parse_statement(self, index):
        try:
            return self.parse_return(index)
        except ParserError as e:
            self.log_error(e)

        return self.parse_expr_statement(index)
                    
    def parse_return(self, index):
        try:
            index = self.match_token(index, token_kinds.return_kw)
        except MatchError:
            err = "expected return keyword"
            raise ParserError(err, index, self.tokens, ParserError.GOT)

        node, index = self.parse_expression(index)
        index = self.expect_semicolon(index)
        return (ast.ReturnNode(node), index)

    def parse_expr_statement(self, index):
        node, index = self.parse_expression(index)
        index = self.expect_semicolon(index)
        return (ast.ExprStatementNode(node), index)
    
    def parse_expression(self, index):
        """Implemented as a shift-reduce parser. Tries to comprehend as much as
        possible of tokens past index as being an expression, and the index
        returned is the first token that could not be parsed into the
        expression. If literally none of it could be parsed as an expression,
        raises an exception like usual.
        """
                
        # Dictionay of key-value pairs {TokenKind: precedence} where higher
        # precedence is higher.
        binary_operators = {token_kinds.plus: 11,
                            token_kinds.star: 12,
                            token_kinds.equals: 1}

        # The set of assignment_tokens (because these are right-associative)
        assignment_operators = {token_kinds.equals}

        # An item in the parsing stack. The item is either a Node or Token,
        # where the node must generate an expression, and the length is the
        # number of tokens consumed in generating this node.
        StackItem = namedtuple("StackItem", ['item', 'length'])
        stack = []

        # TODO: clean up  the if-statements here
        i = index
        while True:
            # If the top of the stack is a number, reduce it to an expression
            # node
            if (stack and isinstance(stack[-1].item, Token)
                and stack[-1].item.kind == token_kinds.number):
                stack[-1] = StackItem(ast.NumberNode(stack[-1].item), 1)
            
            # If the top of the stack is an identifier, reduce it to
            # an identifier node
            elif (stack and isinstance(stack[-1].item, Token)
                and stack[-1].item.kind == token_kinds.identifier):
                stack[-1] = StackItem(ast.IdentifierNode(stack[-1].item), 1)

            # If the top of the stack matches ( expr ), reduce it to a
            # ParenExpr node
            elif (len(stack) >= 3
                  and isinstance(stack[-1].item, Token)
                  and stack[-1].item.kind == token_kinds.close_paren
                  and isinstance(stack[-2].item, ast.Node)
                  and isinstance(stack[-3].item, Token)
                  and stack[-3].item.kind == token_kinds.open_paren):
                expr = stack[-2]
                
                del stack[-3:]
                stack.append(
                    StackItem(ast.ParenExprNode(expr.item), expr.length + 2))

            # If the top of the stack matches a binary operator, reduce it to an
            # expression node.
            elif (len(stack) >= 3
                  and isinstance(stack[-1].item, ast.Node)
                  and isinstance(stack[-2].item, Token)
                  and stack[-2].item.kind in binary_operators.keys()
                  and isinstance(stack[-3].item, ast.Node)

                  # Make sure next token is not higher precedence
                  and not (i < len(self.tokens)
                           and self.tokens[i].kind in binary_operators.keys()
                           and (binary_operators[self.tokens[i].kind] >
                                binary_operators[stack[-2].item.kind]))
                  
                  # Make sure this and next token are not both assignment
                  # tokens, because assignment tokens are right associative.
                  and not (i < len(self.tokens)
                           and stack[-2].item.kind in assignment_operators
                           and self.tokens[i].kind in assignment_operators)):
                left_expr = stack[-3]
                right_expr = stack[-1]
                operator = stack[-2]

                # Remove these last 3 elements
                del stack[-3:]
                stack.append(
                    StackItem(ast.BinaryOperatorNode(left_expr.item,
                                                     operator.item,
                                                     right_expr.item),
                              left_expr.length + operator.length +
                              right_expr.length))
            else:
                # If we're at the end of the token list, or we've reached a
                # token that can never appear in an expression, stop reading.
                # Note we must update this every time the parser is expanded to
                # accept more identifiers.
                if i == len(self.tokens): break
                elif (self.tokens[i].kind != token_kinds.number
                      and self.tokens[i].kind != token_kinds.identifier
                      and self.tokens[i].kind != token_kinds.open_paren
                      and self.tokens[i].kind != token_kinds.close_paren
                      and self.tokens[i].kind not in binary_operators.keys()):
                    break
                
                stack.append(StackItem(self.tokens[i], 1))
                i += 1

        if stack and isinstance(stack[0].item, ast.Node):
            return (stack[0].item, index + stack[0].length)
        else:
            err = "expected expression"
            raise ParserError(err, index, self.tokens, ParserError.GOT)
        
    def parse_declaration(self, index):
        try:
            index = self.match_token(index, token_kinds.int_kw)
        except MatchError:
            err = "expected type name"
            raise ParserError(err, index, self.tokens, ParserError.GOT)

        try:
            index = self.match_token(index, token_kinds.identifier)
        except MatchError:
            err = "expected identifier"
            raise ParserError(err, index, self.tokens, ParserError.AFTER)
            
        variable_name = self.tokens[index-1]
        
        index = self.expect_semicolon(index)

        return (ast.DeclarationNode(variable_name), index)

    def expect_semicolon(self, index):
        """Expect a semicolon at tokens[index]. If one is found, return index+1.
        Otherwise, raise an appropriate ParserError.
        """
        try:
            return self.match_token(index, token_kinds.semicolon)
        except MatchError:
            err = "expected semicolon"
            raise ParserError(err, index, self.tokens, ParserError.AFTER)

    #
    # Utility functions for the parser
    #
    def match_token(self, index, kind_expected):
        """Shorthand for match_tokens for a single token"""
        return self.match_tokens(index, [kind_expected])
        
    def match_tokens(self, index, kinds_expected):
        """Check if self.tokens matches the expected token kinds in order
        starting at the given index. If the tokens all have the expected kind,
        return the index one more than the last parsed element. Otherwise,
        raises a MatchException.

        index (int) - The index at which to begin matching
        kinds_expected (List[TokenKind, None]) - A list of token kinds to expect

        """
        tokens = self.tokens[index:]
        if len(tokens) < len(kinds_expected):
            raise MatchError()
        elif all(kind == token.kind for kind, token
               in zip(kinds_expected, tokens)):
            return index + len(kinds_expected)
        else:
            raise MatchError()

    def log_error(self, error):
        """Log the error in the parser to be used for error reporting. If the
        provided error occurred after parsing no fewer tokens than
        best_error.amount_parsed, replace best_error with the provided error.
        
        error (ParserError) - The error encountered.
        """
        if (not self.best_error or
            error.amount_parsed >= self.best_error.amount_parsed):
            self.best_error = error
